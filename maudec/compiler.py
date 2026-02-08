import sys
from collections import Counter
from collections.abc import Sequence

import maude

from . import ast, diagnostics
from .analyzer import EquationInfo, KindInfo, SortInfo, SymbolInfo, ModuleAnalyzer
from .generators.cpp import CppGenerator
from .identifiers import NameTranslator
from .theories.assoc import AssocDriver
from .theories.builtin import BoolDriver, FloatDriver
from .theories.comm import CommDriver
from .theories.iter import IterDriver
from .theories.driver import Driver
from .theories.integral import IntegralDriver

# Shortcuts
SymbolType = SymbolInfo.Type
Theories = SymbolInfo.Theory

# AST type for the Booleans
BOOL_TYPE = ast.NamedType('bool', builtin=True)


class FreeDriver(Driver):
	"""Driver for operators in the free theory"""

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):
		# Argument are preserved without changes
		return args

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):
		"""Compile a pattern"""
		kind_info = self.compiler.analysis.kind_info[arg.getSort().kind()]
		symbol_info = self.compiler.analysis.symbol_info[arg.symbol()]
		kind_type = ast.NamedType(self.compiler.name_translator(kind_info.name))

		# Whether the arguments are wrapped in a pointer
		recursive_args = symbol_info.recursive_args

		context = self.compiler.analysis.symbol_info[arg.symbol()].sort_signature

		# Compile pattern for every argument
		subpatterns = [self.compiler.compile_pattern(subarg, varmap, conditions,
		                                             context[k], pointer=k in recursive_args)
		               for k, subarg in enumerate(arg.arguments())]

		if topmost:
			return ast.TuplePattern(subpatterns)
		else:
			return ast.ConstructorPattern(kind_type, self.compiler._get_variant(kind_info, arg.symbol()), subpatterns)


class Compiler:
	"""Compile some Maude definitions"""

	def __init__(self, module: maude.Module, analyzer: ModuleAnalyzer, out=sys.stdout, generator=None,
	             name_translator=None):
		self.mod = module
		self.analysis = analyzer
		self.out = out

		# Specific generator for the target language
		self.generator = CppGenerator(out=out) if generator is None else generator
		# Name translator
		self.name_translator = NameTranslator() if name_translator is None else name_translator

		# Variable to expression mapping
		self.varmap: dict[maude.Term, ast.Expr] = {}
		# Shortcuts (i.e. terms that are replaced by variables
		# because they appear at least twice)
		self.shortcuts: dict[maude.Term, ast.Expr] = {}
		# Variables that are not used when compiling a pattern
		self.unused_vars = ()
		# Function renaming for overloading
		self.function_renaming = {}

		# Instantiate theory-specific handlers
		Theories = SymbolInfo.Theory

		self.theory2driver: dict[SymbolInfo.Theory, Driver] = {
			Theories.INTEGRAL: IntegralDriver(self),
			Theories.BOOL: BoolDriver(self),
			Theories.FLOAT: FloatDriver(self),
			Theories.FREE: FreeDriver(self),
			Theories.COMM: CommDriver(self),
			Theories.ITER: IterDriver(self),
			Theories.ASSOC: AssocDriver(self),
			Theories.ACU: AssocDriver(self),  # temporary
		}

		# Builtin theories and types
		self.bool_type = ast.NamedType('bool', builtin=True)
		self.builtin_theories = {Theories.INTEGRAL, Theories.BOOL, Theories.FLOAT}

		# Mapping from some Maude sort to types potentially different from its kind type
		self.special_sorts = {}

	def _get_driver(self, symbol: maude.Symbol):
		"""Get driver for symbol"""

		return self.theory2driver[self.analysis.symbol_info[symbol].theory]

	def _kind_info(self, term: maude.Term):
		"""Get the kind info for a term"""

		return self.analysis.kind_info[term.getSort().kind()]

	def _variant_name(self, symbol: maude.Symbol):
		"""Get the variant corresponding to a symbol"""

		return self.name_translator(str(symbol))

	def _function_name(self, op: SymbolInfo):
		"""Get the name of a function"""

		# Renaming introduced to avoid clashes with overloading
		if fn_name := self.function_renaming.get(op.symb):
			return fn_name

		# By default, use the operator name
		fn_name = str(op.symb)

		if op.type == SymbolInfo.Type.SORT_TEST:
			fn_name = self._sort_test_name(op.props["sort"].sort)

		return self.name_translator(fn_name)

	def _variable_name(self, variable: maude.Term):
		"""Get the name of the variable"""

		return self.name_translator(variable.getVarName())

	def _get_variant(self, info: KindInfo, op: maude.Symbol):
		"""Get variant for an operator"""

		return info.variants[info.ctors_map[op]]

	def compile_expr(self, term: maude.Term, pointer=False) -> ast.Expr:
		"""Compile a Maude term as a expression"""

		# Check whether there is a shortcut for the term
		if shortcut := self.shortcuts.get(term):
			return shortcut

		# We use what we have found in the analysis phase
		info = self.analysis.get_symbol_info(term.symbol())

		# Recursively compile its arguments
		compiled_args = [self.compile_expr(arg, pointer=k in info.recursive_args)
		                 for k, arg in enumerate(term.arguments())]

		# Easy cases: variables and polymorphic operators
		result = None

		match info.type:
			case SymbolType.VARIABLE:
				result = self.varmap[term]

				# Currently, only variables hold points
				if isinstance(result.etype, ast.PointerType) and not pointer:
					result = ast.Dereference(result)

			case SymbolType.EQUALITY:
				result = ast.equals(*compiled_args)

			case SymbolType.INEQUALITY:
				result = ast.Call('!=', compiled_args, self.bool_type,  builtin=True)

			case SymbolType.ITE:
				result = ast.IfExpr(*compiled_args)

			case SymbolType.SORT_TEST:
				sort = info.props['sort']

				if result := self._compile_sort_test(compiled_args[0], sort.sort):
					return result

				return ast.Constant(True, BOOL_TYPE)

			case _:  # theory-specific generation
				if driver := self.theory2driver.get(info.theory):
					result = driver.compile_expr(term, info, compiled_args)

				if result is None:
					result = compiled_args

				# Non-constructor calls
				if not info.ctor and not isinstance(result, ast.Expr):
					return ast.Call(info.special_id or self._function_name(info), result,
					                self._target_type(term.symbol().getRangeSort().kind()),
					                builtin=bool(info.special_id))

		# Basic type without alternative in the same kind
		if not isinstance(result, Sequence):
			return result

		return self._make_constructor(term.symbol(), result)


	def _make_constructor(self, symb: maude.Symbol, args: Sequence[ast.Expr]):
		"""Make a constructor"""

		# Check whether to wrap it or not (only basic types are not wrapped)
		kind_info = self.analysis.kind_info[symb.getRangeSort().kind()]

		kind_type = ast.NamedType(self.name_translator(kind_info.name))
		variant = self._get_variant(kind_info, symb)

		# Wrap arguments that need pointer creation
		new_args = tuple((ast.PointerCtor(value)
		                  if isinstance(vtype, ast.PointerType)
				     and not isinstance(value.etype, ast.PointerType)
				  else value)
		                 for value, vtype in zip(args, variant.types))

		return ast.Constructor(kind_type, variant, new_args)

	def _compile_sort_test(self, arg: ast.Expr, sort: maude.Sort, relative: maude.Sort | None=None):
		"""Compile a sort-membership test"""

		# No test is needed
		if sort == relative:
			return None

		sort_info = self.analysis.get_sort_info(sort)

		# No test is needed
		if sort_info.whole_sort:
			return None

		driver = self.theory2driver.get(sort_info.theory)

		if driver and (result := driver.sort_test(arg, sort)):
			return result

		return ast.Call(self._sort_test_name(sort), (arg,),
				BOOL_TYPE, builtin=False)


	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
			    context: maude.Sort | Sequence[maude.Sort], topmost=False, pointer=False):
		"""Compile a matching argument"""

		# Check whether to wrap it or not (only basic types are not wrapped)
		kind_info = self.analysis.kind_info[arg.symbol().getRangeSort().kind()]

		# Wrapped single element in associative operator
		if isinstance(context, maude.Sort) and context in kind_info.props.get('list_sorts', ()) and arg.getSort() <= context and arg.getSort() != context:
			return ast.SequencePattern(((self.compile_pattern(arg, varmap, conditions, arg.getSort(), topmost=topmost, pointer=pointer), False),))

		# The argument is a variable
		if arg.isVariable():
			var_type = self._target_type(arg.symbol().getRangeSort().kind())
			var_name = self._variable_name(arg)

			# Whether the variable is used somewhere
			is_used = arg not in self.unused_vars

			# Wrap types that are behind a pointer
			if pointer:
				var_type = ast.PointerType(var_type)

			# Not yet bound
			if (other := varmap.get(arg)) is None:
				var_expr = ast.Variable(self._variable_name(arg), var_type)
				varmap[arg] = var_expr

				# Add sort-membership conditions if any
				if cond := self._compile_sort_test(var_expr, arg.getSort(), relative=context):
					conditions.append(cond)
					is_used = True

			# Already bound, so we must check compatibility
			else:
				# Inefficient way of making fresh variable name (TODO improve)
				fresh_name = next(vname for k in range(2, 10000) if (vname := f'{var_name}_{k}') not in varmap)
				var_expr = ast.Variable(fresh_name, var_type)
				conditions.append(ast.equals(other, var_expr))

			return ast.VariablePattern(var_expr, used=is_used)

		# Theory-specific handling
		driver = self._get_driver(arg.symbol())
		pattern = driver.compile_pattern(arg, varmap, conditions, context, topmost=topmost)

		# Basic type without alternative in the same kind
		if not isinstance(pattern, Sequence):
			return pattern

		kind_type = ast.NamedType(self.name_translator(kind_info.name))
		variant = self._get_variant(kind_info, arg.symbol())

		return ast.ConstructorPattern(kind_type, variant, pattern)

	def compile_condition(self, cf: maude.ConditionFragment, k: int, body, conditions):
		"""Compile condition fragment"""

		lhs_symbol = cf.getLhs().symbol()

		match cf:
			case maude.EqualityCondition():
				compiled_lhs = self.compile_expr(cf.getLhs())
				rhs = cf.getRhs()
				rhs_info = self.analysis.symbol_info[rhs.symbol()]

				# expr = true conditions are translated to expr
				if rhs_info.type == SymbolType.BOOLEAN_LITERAL and rhs_info.props['value']:
					conditions.append(compiled_lhs)
				else:
					conditions.append(ast.equals(compiled_lhs, self.compile_expr(rhs)))

			case maude.SortTestCondition():
				compiled_lhs = self.compile_expr(cf.getLhs())

				# Add the membership constraints to the condition
				if cond := self._compile_sort_test(compiled_lhs, cf.getSort()):
					conditions.append(cond)

			case maude.AssignmentCondition():
				lhs = cf.getLhs()
				rhs = self.compile_expr(cf.getRhs())

				# TODO: this needs revision

				if lhs.isVariable():
					if other := self.varmap.get(lhs):
						conditions.append(ast.equals(other, rhs))
					else:
						# Introduce a new variable, even if it has only one use
						var_expr = ast.Variable(self.name_translator(lhs.getVarName()),
						                        self._target_type(lhs.symbol().getRangeSort().kind()))
						self.varmap[lhs] = var_expr
						body.append(ast.VarDecl(var_expr, rhs))

				else:
					# Introduce a new variable for the result of the right-hand side
					var_expr = ast.Variable(f'ac{k}', self._target_type(lhs.symbol()))
					self.varmap[lhs] = var_expr

					driver = self._get_driver(lhs.symbol())
					# TODO: to be done
					driver.compile_pattern(lhs, var_expr, self, conditions, ())

			case maude.RewriteCondition():
				diagnostics.warning('rewrite conditions are not supported, they will be ignored')

	def compile_equation(self, eq_info: EquationInfo, context: Sequence[maude.Sort], topmost=True):
		"""Compile an equation for certain symbol"""

		# Map Maude variables to the target language code
		self.varmap = {}
		conditions, body = [], []

		eq = eq_info.eq

		# Get patterns from the LHS
		self.unused_vars = eq_info.lhs_vars - eq_info.rhs_vars - eq_info.condition_vars - eq_info.non_linear_vars
		patterns = self.compile_pattern(eq.getLhs(), self.varmap, conditions, context, topmost=topmost)
		self.unused_vars = ()

		# Compile condition
		for k, cf in enumerate(eq.getCondition()):
			self.compile_condition(cf, k, body, conditions)

		# Define repeated expressions as variables
		for k, term in enumerate(eq_info.repeated, start=1):
			# TODO: avoid name clashes
			aux_var = ast.Variable(f'aux{k}', self._target_type(term.getSort().kind()))

			# Introduce a variable definition
			body.append(ast.VarDecl(aux_var, self.compile_expr(term), constant=True))

			# Define for the translation of other variables and the main body
			self.shortcuts[term] = aux_var

		body.append(self.compile_expr(eq.getRhs()))

		self.shortcuts.clear()

		return ast.MatchBranch(patterns, ast.conjunction(conditions), body)

	def _target_type(self, kind: maude.Kind | maude.Sort):
		"""Target type for a kind"""

		# Compile to the target sort
		if isinstance(kind, maude.Sort):
			if target := self.special_sorts.get(kind):
				return target
			kind = kind.kind()

		info = self.analysis.kind_info[kind]

		# If the kind consists of a builtin type only
		if len(info.ctors) == 1 and info.ctors[0].theory in self.builtin_theories:
			return self._signature(info.ctors[0])[0]

		return ast.NamedType(self.name_translator(info.name))

	def _signature(self, ctor: SymbolInfo):
		"""Target type for a symbol"""

		# Special constructors
		match ctor.type:
			case SymbolType.BOOLEAN_LITERAL:
				return self.bool_type,

			case SymbolType.FLOAT_LITERAL:
				return ast.NamedType('float', builtin=True),

			case SymbolType.SUCCESSOR:
				return ast.NamedType('int', builtin=True),

		# Theories
		match ctor.theory:
			case Theories.ASSOC:
				return ast.SequenceType(self._target_type(ctor.symb.getRangeSort().kind())),

			case Theories.ITER:
				return ast.PointerType(self._target_type(ctor.sort_signature[0])), ast.NamedType('nat', builtin=True)

		args = [self._target_type(sort) for sort in ctor.sort_signature[:-1]]

		# Wrap recursive arguments in pointer types
		for k in ctor.recursive_args:
			args[k] = ast.PointerType(args[k])

		return tuple(args)

	def _deduce_argument_names(self, sym: maude.Symbol, info: SymbolInfo):
		"""Figure out argument names for the symbol"""

		arg_names = [f'a{k + 1}' for k in range(sym.arity())]

		# Find appropriate names or the arguments
		seen_vars = set()

		for eq in info.top_equations:
			for k, arg in enumerate(eq.eq.getLhs().arguments()):
				if arg.isVariable() and (name := arg.getVarName()) not in seen_vars:
					arg_names[k] = name
					seen_vars.add(name)

		return arg_names

	def _make_declaration(self, info: SymbolInfo):
		"""Construct a function declaration"""

		sym = info.symb

		# Get the kinds of the operator
		kinds = [sym.domainKind(k) for k in range(sym.arity())]
		arg_names = self._deduce_argument_names(sym, info)

		# Sort signature
		sort_signature = info.sort_signature if info.sort_signature else (kinds + [sym.getRangeSort().kind()])

		# AST node for the function declaration
		domain = tuple((self._target_type(sort), arg) for sort, arg in zip(sort_signature, arg_names))
		signature = ast.FunctionDecl(self._function_name(info), domain,
		                             self._target_type(sort_signature[-1]))

		return kinds, signature

	def _sort_test_name(self, sort: maude.Sort):
		"""Name of the sort test for a sort"""

		# TODO: accumulate which need to be generated
		return self.name_translator(f"hasSort_{self.name_translator(str(sort))}")

	def _compile_sort_predicate(self, info: SortInfo):
		"""Compile a sort test"""

		signature = ast.FunctionDecl(
			self._sort_test_name(info.sort),
			((self._target_type(info.sort.kind()), 'obj'),),
			BOOL_TYPE,
		)

		kind_info = self.analysis.kind_info[info.sort.kind()]

		# The sort covers all the kind
		TRUE = ast.Constant(True, BOOL_TYPE)

		if info.sort == kind_info.whole_sort:
			body = [TRUE]

		else:
			branches = []
			kind_type = ast.NamedType(self.name_translator(kind_info.name))

			# Add a branch for each constructor
			for ctor in info.ctors:
				varmap, conditions = {}, []

				for decl in ctor.symb.getOpDeclarations():
					# Build a term to compile the pattern
					domain = list(decl.getDomainAndRange())[:-1]
					term = ctor.symb(*(self.mod.parseTerm(f'a{k}:{sort}') for k, sort in enumerate(domain)))

					varmap, conditions = {}, []
					pattern = self.compile_pattern(term, varmap, conditions, ctor.sort_signature, topmost=False)
					branches.append(ast.MatchBranch(pattern, condition=None, body=[TRUE]))

			# Add a branch for each membership axiom
			for mb in info.memberships:
				varmap, conditions = {}, []
				lhs = mb.getLhs()
				pattern = self.compile_pattern(lhs, varmap, conditions, self.analysis.symbol_info[lhs.symbol()].sort_signature, topmost=False)

				for k, cf in enumerate(mb.getCondition()):
					conditions.append(self.compile_condition(cf, k, [], conditions))

				branches.append(ast.MatchBranch(pattern, ast.conjunction(conditions), [TRUE]))



			# Add a final negative branch
			branches.append(ast.MatchBranch(ast.VariablePattern(ast.Variable('a', kind_type), used=False),
			                                condition=None, body=[ast.Constant(False, BOOL_TYPE)]))

			body = [ast.MatchExpr(ast.Variable('obj', signature.args[0][0]), branches)]

		self.generator.generate_function(signature, body)

	def compile_symbol(self, sym: maude.Symbol, info: SymbolInfo):
		"""Compile a given symbol"""

		# Sort-membership tests are compiled apart
		if info.type == SymbolType.SORT_TEST:
			self._compile_sort_predicate(info.props['sort'])

		# Compilation driver for the symbol
		driver = self.theory2driver.get(sym.getRangeSort().kind())

		# Ignore operators without equations or which are natively handled
		if len(info.top_equations) == 0 or (driver and driver.handled(sym)):
			return

		# Make the function declaration
		kinds, signature = self._make_declaration(info)

		# Get arguments that are always variables
		# (only for the free theory)
		var_arguments = [True] * len(kinds)
		seen_vars = [None] * len(kinds)

		if info.theory == Theories.FREE:
			for eq in info.top_equations:
				for k, arg in enumerate(eq.eq.getLhs().arguments()):
					if not arg.isVariable():
						var_arguments[k] = False
					elif seen_vars[k] is None:
						seen_vars[k] = arg
					elif seen_vars[k] != arg:
						var_arguments[k] = False

		else:
			var_arguments = [False] * len(kinds)

		branches = []

		for eq in info.top_equations:
			# Compile the equation LHS and implicit and explicit conditions
			branches.append(self.compile_equation(eq, info.sort_signature))

		# Add an error recovery branch
		kind_info = self.analysis.kind_info[sym.getRangeSort().kind()]

		if err_variant := self._kind_has_err(kind_info) and any(self._kind_has_err(k) for k in kinds):
			kind_type = ast.NamedType(self.name_translator(kind_info.name))
			branches.append(ast.MatchBranch(
				ast.VariablePattern(ast.Variable('fallback', kind_type), used=False),
				condition=None,
				body=[ast.Constructor(kind_type, err_variant, ())],
			))


		# Single expression without case distinctions
		if len(branches) == 1 and all(var_arguments):
			body = branches[0].body

		# Use a match expression
		else:
			if any(var_arguments):
				for branch in branches:
					if isinstance(branch.pattern, ast.TuplePattern):
						branch.pattern = ast.TuplePattern(
							tuple(p for p, only_var in zip(branch.pattern.args, var_arguments) if not only_var))

			inputs = ast.Tuple(tuple(ast.Variable(name, atype)
			                         for (atype, name), var_only in zip(signature.args, var_arguments)
			                         if not var_only))

			body = [self._match_simplify(ast.MatchExpr(inputs, branches))]

		self.generator.generate_function(signature, body)

	def _kind_has_err(self, kinfo: KindInfo):
		"""Check whether a kind has error variant"""

		if hasattr(kinfo, 'variants'):
			if kinfo.variants[-1].name == 'Err' and not kinfo.variants[-1].types:
				return kinfo.variants[-1]

	def _match_simplify(self, node: ast.MatchExpr):
		"""Simplify a match construct"""

		# Remove unneeded tuple wrapping
		if isinstance(node.expr, ast.Tuple) and len(node.expr.args) == 1:
			node.expr = node.expr.args[0]

			for case in node.cases:
				if isinstance(case.pattern, ast.TuplePattern):
					case.pattern = case.pattern.args[0]

		return node

	def compile_tests(self, tests):
		"""Compile all tests"""

		compiled_tests = []

		for test in tests:
			match test:
				case 'e', lhs, rhs:
					compiled_tests.append(('e', f'assertEquals({lhs}, {rhs})', self.compile_expr(lhs), self.compile_expr(rhs)))

				case ('t' | 'f'), term:
					name = str(test[0] == 't')
					compiled_tests.append((test[0], f'assert{name}({term})', self.compile_expr(term)))

				case 's', term, sort:
					if test := self._compile_sort_test(self.compile_expr(term), sort):
						compiled_tests.append(('t', f'assertLeqSort({term}, {sort})', test))


		self.generator.generate_tests(tuple(compiled_tests))

	def compile_rules(self):
		"""Compile rules in the module"""

		# Compile a rule function for each kind (to apply rules on top)
		for kinfo in self.analysis.kind_ordering:
			# Only kinds with rules
			if not kinfo.rules:
				continue

			# Declare a function for a single rule application
			kind_type = self._target_type(kinfo.kind)
			argument_name = 'term'

			decl = ast.FunctionDecl(
				f'rlapp_{kinfo.name}',
				((kind_type, argument_name),),
				ast.OptionalType(kind_type),
			)

			# Build a match expression with a case for each rule
			branches = []

			for rl in kinfo.rules:
				# Compile the rule like an equation
				branches.append(self.compile_equation(rl, rl.eq.getLhs().getSort(), topmost=False))

				# Wrap the result in an optional value
				# (we assume there are not returns in the generated code, which is currently true)
				branches[-1].body[-1] = ast.OptionalValue(kind_type, branches[-1].body[-1])

			# Add fallback branch (no rewrite)
			branches.append(ast.MatchBranch(
				ast.VariablePattern(ast.Variable('dc', kind_type), used=False),
				condition=None,
		 		body=[ast.OptionalValue(kind_type)],
			))

			self.generator.generate_function(decl, [ast.MatchExpr(ast.Variable(argument_name, kind_type), branches)])

		# Compile searches to apply rules anywhere
		for kinfo in self.analysis.kind_ordering:
			# Only rewritable kinds
			if not kinfo.rewritable:
				continue

			# Declare the function
			kind_type = self._target_type(kinfo.kind)
			argument_name = 'term'

			decl = ast.FunctionDecl(
				f'arlapp_{kinfo.name}',
				((kind_type, argument_name),),
				ast.OptionalType(kind_type),
			)

			body = []


			# Application inside the arguments
			branches = []

			for ctor in kinfo.ctors:
				# Arguments in which we can rewrite
				frozen = set(ctor.symb.getFrozen())
				subrew = [k for k in range(ctor.symb.arity())
				          if k not in frozen and self.analysis.kind_info[ctor.symb.domainKind(k)].rewritable]

				# Nothing to rewrite
				if not subrew or ctor.theory in self.builtin_theories:
					continue

				variant = self._get_variant(kinfo, ctor.symb)
				arguments = tuple(ast.Variable(f't{k}', ttype) for k, ttype in enumerate(variant.types))

				subbody = []

				for index in subrew:
					subkind = self.analysis.kind_info[ctor.symb.domainKind(index)]
					is_pointer = isinstance(variant.types[index], ast.PointerType)

					# Variable to hold the contents of the optional value
					capture = ast.Variable('subt', self._target_type(subkind.kind))
					# Arguments with the replacement
					new_args = list(arguments)
					new_args[index] = ast.PointerCtor(capture) if is_pointer else capture
					argument = ast.Dereference(arguments[index]) if is_pointer else arguments[index]

					subbody.append(ast.IfOptional(
						ast.Call(f'arlapp_{subkind.name}', (argument,),
					                 ast.OptionalType(self._target_type(subkind.kind)), builtin=False),
						[ast.Return(ast.OptionalValue(decl.result_type, ast.Constructor(kind_type, variant, tuple(new_args))))],
						capture = capture
					))

				# TODO: take axioms into account

				branches.append(ast.MatchBranch(ast.ConstructorPattern(kind_type, variant, tuple(ast.VariablePattern(arg) for arg in arguments)),
				                condition=None, body=subbody))

			if branches:
				body.append(ast.MatchStmt(ast.Variable(argument_name, kind_type), branches))

			# Rule application on top (at the end, innermost)
			if kinfo.rules:
				body.append(ast.Call(f'rlapp_{kinfo.name}', (ast.Variable(argument_name, kind_type),),
				                     decl.result_type, builtin=False))
			else:
				body.append(ast.OptionalValue(decl.result_type, None))

			self.generator.generate_function(decl, body)

	def compile_normalizer(self, info: SymbolInfo):
		"""Compile normalization functions for some symbols"""

		kind_info = self.analysis.kind_info[info.symb.getRangeSort().kind()]
		kind_type = self._target_type(info.sort_signature[-1])
		variant = self._get_variant(kind_info, info.symb)

		decl = ast.FunctionDecl(
			f'make_{self._function_name(info)}',
			tuple((ty, f'a{k}') for k, ty in enumerate(variant.types, start=1)),
			kind_type,
		)

		if info.theory == Theories.COMM:
			a1, a2 = ast.Variable('a1', kind_type), ast.Variable('a2', kind_type)

			# TODO: move to the driver
			body = ast.IfExpr(
				ast.Call('<=', (a1, a2), BOOL_TYPE, builtin=True),
				ast.Constructor(kind_type, variant, (a1, a2)),
				ast.Constructor(kind_type, variant, (a2, a1)),
			)

		# Theories.ITER
		else:
			a1, a2 = ast.Variable('a1', kind_type), ast.Variable('a2', ast.NamedType('nat', builtin=True))
			a3, a4 = ast.Variable('a3', kind_type), ast.Variable('a4', ast.NamedType('nat', builtin=True))

			body = ast.MatchExpr(ast.Dereference(a1), [
				ast.MatchBranch(ast.ConstructorPattern(kind_info, variant, (ast.VariablePattern(a3), ast.VariablePattern(a4))),
					condition=None,
					body=[ast.Constructor(kind_type, variant, (a3, ast.Call('+', (a2, a4), a2.etype, builtin=True)))]
				),
				ast.MatchBranch(ast.VariablePattern(a1, used=False), condition=None, body=[
					ast.Constructor(kind_type, variant, (a1, a2))
				]),
			])

		self.generator.generate_function(decl, [body])

	def compile(self, filter=None, tests=()):
		"""Compile all elements of the module"""

		# Build the translation for specific sorts
		for info in self.analysis.kind_ordering:
			for sort in info.sorts.values():
				if driver := self.theory2driver.get(sort.theory):
					if ttype := driver.target_type(sort.sort, info):
						self.special_sorts[sort.sort] = ttype

		# Generate type definitions
		for info in self.analysis.kind_ordering:
			# We generate sum type declarations for each kind, except pure
			# builtin ones, even if there is only one alternative. This may be
			# needed in the target language to deal with overloaded function
			# names on different kinds
			if len(info.ctors) != 1 or info.ctors[0].theory not in self.builtin_theories:
				variants = [ast.Variant(self._variant_name(op.symb),
				                        self._signature(op)) for k, op in enumerate(info.ctors)
					   if k not in info.hidden_ctors]

				# If not error free, put an error case
				if not info.kind.errorFree():
					variants.append(ast.Variant('Err', ()))

				self.generator.generate_type(ast.SumTypeDecl(self.name_translator(info.name), variants), info)

				# Store type variants for future use
				info.variants = variants

		# Adaptation when function overloading is not available
		if hasattr(self.generator, 'supports_function_overloading') and not self.generator.supports_function_overloading():
			# This may not be deterministic and names may clash
			repeats = Counter()

			for info in self.analysis.symbol_ordering:
				if not info.ctor and not info.special_id:
					symbol_name = str(info.symb)

					if repeats[symbol_name] != 0:
						self.function_renaming[info.symb] = f'{self._function_name(info)}_{repeats[info] + 1}'

					repeats[symbol_name] += 1

		# Generate forward declarations (for language that need them)
		if hasattr(self.generator, 'forward_declare_function'):
			for info in self.analysis.symbol_ordering:
				if info.scc is not None:
					_, declaration = self._make_declaration(info)
					self.generator.forward_declare_function(declaration)

		# Generate normalization functions (modulo axioms)
		for info in self.analysis.symbol_ordering:
			if info.theory in (Theories.COMM, Theories.ITER) and info.ctor:
				self.compile_normalizer(info)

		# Generate the code for each symbol
		for info in self.analysis.symbol_ordering:
			# Skip symbols that do not pass the filter
			if not filter or filter.match(str(info.symb)):
				self.compile_symbol(info.symb, info)

		# Generate code for the rules
		self.compile_rules()

		# Compile tests (if any)
		if tests:
			self.compile_tests(tests)

		self.generator.finish()
