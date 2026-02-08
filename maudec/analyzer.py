#
# Analyze modules for compilation
#

from collections import defaultdict
from collections.abc import Mapping
from enum import Enum
from graphlib import TopologicalSorter

import maude

from .utils import compute_scc, dfs

# Pseudospecial operators in the Bool kind
# (while defined by equations in Maude, we want to use the
# predefined operators in the target language)
BOOL_PSEUDOSPECIALS = frozenset({'_and_', '_or_', '_xor_', 'not_', '_implies_'})


def visit_subterms(t: maude.Term, fn):
	"""Visit all subterms of a Maude term (but the term itself)"""

	stack = [iter(t.arguments())]

	while stack:
		next_t = next(stack[-1], None)

		if next_t is not None:
			if fn(next_t):
				stack.append(iter(next_t.arguments()))

		else:
			stack.pop()


def subterms(t: maude.Term):
	"""Iterate over all subterms of a Maude term (but the term itself)"""

	stack = [iter(t.arguments())]

	while stack:
		next_t = next(stack[-1], None)

		if next_t is not None:
			yield next_t
			stack.append(iter(next_t.arguments()))

		else:
			stack.pop()


def collect_symbols(t: maude.Term):
	"""Collect all symbols in a term"""

	return {(t.symbol(), True)} | {(st.symbol(), False) for st in subterms(t)}


def collect_variables(t: maude.Term, variables=None):
	"""Collect all variables in a term"""

	# The caller can also provide a set to acumulate on
	if variables is None:
		variables = set()

	if t.isVariable():
		variables.add(t)

	for st in subterms(t):
		if st.isVariable():
			variables.add(st)

	return variables


def find_nonlinear_vars(t: maude.Term):
	"""Find non-linear variables in a term"""

	# The caller can also provide a set to accumulate on
	seen, nonlinear = set(), set()

	if t.isVariable():
		seen.add(t)

	for st in subterms(t):
		if st.isVariable():
			if st in seen:
				nonlinear.add(st)
			else:
				seen.add(st)

	return nonlinear


def find_repeated_subterms(term: maude.Term):
	"""Find repeated subterms in a term"""

	# Also known as Common Subexpression Elimination

	seen, repeated = set(), set()

	def catch_repeated(subterm: maude.Term):
		# Do not consider variables and constants
		# (this should be refined, 0-ary terms may not be constants)
		if subterm.symbol().arity() > 0:
			# Repeated term, we do not continue recursively
			if subterm in seen:
				repeated.add(subterm)
			# New term, we continue recursively
			else:
				seen.add(subterm)
				return True

		return False

	visit_subterms(term, catch_repeated)

	# Order repeated terms
	if len(repeated) <= 1:
		return tuple(repeated)

	edges = {}

	def catch_included(subterm: maude.Term):
		# Like the previous one
		if subterm.symbol().arity() > 0:
			if subterm in repeated:
				seen.add(subterm)
			else:
				return True

		return False

	for t in repeated:
		seen.clear()
		visit_subterms(t, catch_included)

		edges[t] = set(seen)

	return tuple(TopologicalSorter(edges).static_order())


def _is_ctor(symb: maude.Symbol):
	"""Check whether a symbol is a constructor"""

	return any(decl.isConstructor() for decl in symb.getOpDeclarations())


class SymbolInfo:
	"""Maude symbol/operator data"""

	class Type(Enum):
		NORMAL = 0           # no particular meaning
		SPECIAL = 1          # special symbol
		BOOLEAN_LITERAL = 2  # true/false constant in Bool
		ZERO = 4             # zero constant in Integral
		SUCCESSOR = 5        # successor operator in Integral
		FLOAT_LITERAL = 6    # floating-point literal
		STRING = 7           # string literal
		VARIABLE = 8         # variable
		EQUALITY = 9         # instance of polymorphic equality operator
		INEQUALITY = 10      # instance of polymorphic inequality operator
		ITE = 11             # instance of polymorphic if-then-else operator
		MINUS = 12           # minus operator in Integral
		SORT_TEST = 13       # sort test symbol

	class Theory(Enum):
		BOOL = 0      # Booleans (native type)
		INTEGRAL = 1  # Natural and integer numbers (native type)
		FLOAT = 2     # Floating-point numbers (native type)
		FREE = 3      # Free theory (tuples)
		ASSOC = 4     # Associative (lists)
		AU = 5        # Associative with identity element (lists)
		ACU = 6       # Associative, commutative and with identity (multisets)
		ITER = 7      # Iterative symbol
		COMM = 8      # commutative symbol (only)

	def __init__(self, symb: maude.Symbol, stype: Type):
		self.symb = symb  # Maude symbol object
		self.ctor = False  # whether the symbol is a constructor
		self.type = stype  # symbol type
		self.theory = self.Theory.FREE  # symbol theory
		self.special_id: str | None = None  # special identifier
		self.top_equations = []  # equations that have it on top
		self.nested_equations = []  # equations where it appears inside
		self.props = {}  # properties (in free form)

		self.recursive_args = ()  # recursive arguments (only for constructors)

		# Strongly-connected components of the call graph
		self.scc = None
		self.scc_number = None

		# Coverage of the signature by sorts
		self.sort_signature = None

	def __str__(self):
		equations = f' with {len(self.top_equations)} equations' if self.top_equations else ''
		if self.nested_equations:
			equations += f' (appears in {len(self.nested_equations)})'
		return f'{self.type.name.lower()}{" ctor" if self.ctor else ""}{equations}'

	def dump(self):
		"""Dump information about the symbol"""

		return {
			'name': str(self.symb),
			'ctor': self.ctor,
			'type': self.type.name.lower(),
			'theory': self.theory.name.lower(),
			'special': self.special_id,
			'scc': self.scc_number,
			'recursive_args': sorted(self.recursive_args),
			'sort_signature': [str(s) for s in self.sort_signature] if self.sort_signature else None,
		}


class KindInfo:
	"""Information about a Maude kind"""

	def __init__(self, kind: maude.Kind):
		# Maude kind
		self.kind = kind
		# Safe name for the target language type
		self.name = str(kind.sort(1))
		# Constructors with their symbol info
		self.ctors = []
		self.ctors_map = {}
		# Constructors that are not part of the target type
		self.hidden_ctors = set()
		# Properties (in free form)
		self.props = {}
		# Own sorts
		self.sorts = {str(sort): SortInfo(sort) for k in range(kind.nrSorts())
		              if (sort := kind.sort(k)) and str(sort)[0] != '['}
		# Rules in this kind
		self.rules = []

		# Indices of the sorts
		self.sort_indices = {kind.sort(k): k for k in range(kind.nrSorts())}

		# Is wrapped?
		self.wrapped = True
		# Strongly-connected component by kind usage
		self.scc = None
		self.scc_number = None
		# Kind dependencies
		self.depends = set()
		# Whether it is rewritable (including subterms)
		self.rewritable = False
		# Sort that includes the whole kind (if any)
		self.whole_sort = kind.sort(1) if kind.nrMaximalSorts() == 1 else None

		# Mark whole sort (if any)
		if self.whole_sort is not None:
			self.sorts[str(self.whole_sort)].whole_sort = True

	def add_ctor(self, info: SymbolInfo):
		"""Add a constructor to the kind"""

		self.ctors_map[info.symb] = len(self.ctors)
		self.ctors.append(info)

	def __repr__(self):
		return f'KindInfo({self.kind})'

	def dump(self):
		"""Dump information about the kind"""

		return {
			'name': self.name,
			'wrapped': self.wrapped,
			'ctors': [str(op.symb) for op in self.ctors],
			'sorts': {name: info.dump() for name, info in self.sorts.items()},
			'depends': [d.name for d in self.depends],
			'scc': self.scc_number,
			'error_free': self.kind.errorFree(),
			'whole_sort': str(self.whole_sort) if self.whole_sort else None,
		}


class SortInfo:
	"""Information about a Maude sort"""

	def __init__(self, sort: maude.Sort):
		# Maude sort
		self.sort = sort
		# Constructors with their term info
		self.ctors = []
		# Membership in which it is involved
		self.memberships = []
		# Subsorts
		self.subsorts = ()
		# Theory associated to this sort
		self.theory = None
		# Whether the sort covers the whole kind
		self.whole_sort = False

	def __repr__(self):
		return f'SortInfo({self.sort})'

	def dump(self):
		"""Dump information about the sort"""

		return {
			'name': str(self.sort),
			'subsorts': [str(sort.sort) for sort in self.subsorts],
			'ctors': [str(op.symb) for op in self.ctors],
			'memberships': len(self.memberships),
			'theory': self.theory,
			'whole_sort': self.whole_sort,
		}


class EquationInfo:
	"""Information about an equation or rule"""

	def __init__(self, eq: maude.Equation | maude.Rule):
		self.eq = eq

		# Calculate which variables appear in each side
		self.rhs_vars = collect_variables(eq.getRhs())
		self.lhs_vars = collect_variables(eq.getLhs())
		self.condition_vars = set()

		for cf in eq.getCondition():
			collect_variables(eq.getLhs(), variables=self.condition_vars)

			if not isinstance(cf, maude.SortTestCondition):
				collect_variables(eq.getRhs(), variables=self.condition_vars)

		# Non-linear variables
		self.non_linear_vars = find_nonlinear_vars(eq.getLhs())

		# Find repeated subterms
		self.repeated = find_repeated_subterms(eq.getRhs())

	def called_symbols(self):
		"""Symbols called in this equation"""

		symbols = {s for s, _ in collect_symbols(self.eq.getRhs())}

		for cf in self.eq.getCondition():
			symbols.update(s for s, _ in collect_symbols(cf.getLhs()))

			if not isinstance(cf, maude.SortTestCondition):
				symbols.update(s for s, _ in collect_symbols(cf.getRhs()))

		return symbols

	@property
	def from_condition_vars(self):
		"""Variables introduced by the condition"""

		return self.condition_vars - self.lhs_vars


class StrategyInfo:
	"""Information about a rewriting strategy"""

	def __init__(self, strat: maude.RewriteStrategy):
		self.strat = strat
		self.defs = []

	def dump(self):
		"""Dump information about the strategy"""

		return {
			'name': str(self.strat),
		}


class ModuleAnalyzer:
	"""Analyze Maude modules"""

	def __init__(self, module: maude.Module):
		self.mod = module

		# Information for every sort
		self.sort_info = {}
		# Information for every symbol
		self.symbol_info = {}
		# Information for every kind
		self.kind_info = {}
		# Information about strategies
		self.strat_info = {}
		# Issues found during analysis
		self.issues = []

		# Kind of the basic types
		self.int_kind = None
		self.bool_kind = None
		self.float_kind = None

		# Dependency-safe (except circular dependencies) ordering of kinds
		self.kind_ordering = ()

		self._analyze()

	def _handle_successor(self, symb: maude.Symbol, op: SymbolInfo):
		"""Handle the successor symbol"""

		# Declaration of the special symbol (it must be one)
		op_decl = next(iter(symb.getOpDeclarations()), None)

		if op_decl is None:
			self.issues.append((symb, 'no operator declaration for this symbol'))
			return op

		op.type = SymbolInfo.Type.SUCCESSOR
		op.theory = SymbolInfo.Theory.INTEGRAL
		op.ctor = True
		self.int_kind = symb.getRangeSort().kind()

		# Save the relevant sorts
		kind_info = self.kind_info[self.int_kind]
		domain_range = list(op_decl.getDomainAndRange())
		nat_sort = domain_range[0]
		kind_info.props['NatSort'] = nat_sort
		kind_info.props['NzNatSort'] = domain_range[-1]

		kind_info.sorts[str(nat_sort)].theory = SymbolInfo.Theory.INTEGRAL
		kind_info.sorts[str(domain_range[-1])].theory = SymbolInfo.Theory.INTEGRAL

		# Save the zero constant
		zero = symb.getTermHooks().get('zeroTerm')
		op.props['zero'] = zero

		if zero is None:
			self.issues.append((symb, 'missing zero annotation in the successor symbol'))
			return op

		kind_info.props['ZeroSort'] = zero.getSort()
		kind_info.sorts[str(zero.getSort())].theory = SymbolInfo.Theory.INTEGRAL

		# Zero may have been inserted as a normal operator in the
		# constructor list, so we remove it. However, we may need
		# to reintroduce if the Nat sort is spoilt by other constructors.
		kind_info.ctors.remove(self.symbol_info[zero.symbol()])

		# Insert the information for the zero symbol
		self.symbol_info[zero.symbol()].type = SymbolInfo.Type.ZERO
		self.symbol_info[zero.symbol()].theory = SymbolInfo.Theory.INTEGRAL

		# Add it as a constructor to the kind
		kind_info.add_ctor(op)
		# Link both successor and zero to a common pseudoconstructor
		kind_info.ctors_map[zero.symbol()] = kind_info.ctors_map[op.symb]

	def _index_special(self, symb: maude.Symbol):
		"""Handle special operators"""

		# In practice, there is only one hook per symbol
		hook = symb.getIdHooks()[0]
		name = hook[0]

		# MaudeSymbolInfo of special type
		op = SymbolInfo(symb, SymbolInfo.Type.SPECIAL)

		# Hook identifiers may include arguments (usual with arithmetic operators),
		# so we build an identifier including them for our purposes
		op.special_id = ':'.join(hook)

		# Range sort
		range_sort = symb.getRangeSort()

		match name:
			# true and false constants
			case 'SystemTrue' | 'SystemFalse':
				op.type = SymbolInfo.Type.BOOLEAN_LITERAL
				op.props['value'] = name == 'SystemTrue'
				op.theory = SymbolInfo.Theory.BOOL
				op.ctor = True
				self.bool_kind = range_sort.kind()
				kind_info = self.kind_info[self.bool_kind]
				kind_info.props['true' if name == 'SystemTrue' else 'false'] = op

				# Add the constructor of Boolean literals to its kind
				if name == 'SystemTrue':
					kind_info.add_ctor(op)

			case 'EqualitySymbol' | 'CommutativeDecomposeEqualitySymbol':
				# CommutativeDecomposeEqualitySymbol is _.=._, the initial
				# equality predicate, but coincides with equality on ground terms.

				# Both _==_ and _=/=_ are EqualitySymbol, but the equalTerm term hook
				# is true in the first case and false in the second.
				is_true = symb.getTermHooks()['equalTerm'].symbol().getIdHooks()[0][0] == 'SystemTrue'
				op.type = SymbolInfo.Type.EQUALITY if is_true else SymbolInfo.Type.INEQUALITY

			# The polymorphic if_then_else_fi
			case 'BranchSymbol':
				op.type = SymbolInfo.Type.ITE

			# Successor for natural numbers
			case 'SuccSymbol':
				self._handle_successor(symb, op)

			# Unary minus constructor for integers
			case 'MinusSymbol':
				op.type = SymbolInfo.Type.MINUS
				op.theory = SymbolInfo.Theory.INTEGRAL

				self.kind_info[symb.getRangeSort().kind()].props['IntSort'] = symb.getRangeSort()

			# Float literals pseudo-symbol
			case 'FloatSymbol':
				self.float_kind = range_sort.kind()
				self.sort_info[range_sort] = 'double'

				op.type = SymbolInfo.Type.FLOAT_LITERAL
				op.theory = SymbolInfo.Theory.FLOAT

				# Add it as a constructor to its kind
				self.kind_info[self.float_kind].add_ctor(op)

			# Float operations
			case 'FloatOpSymbol':
				op.theory = SymbolInfo.Theory.FLOAT

			# Rationals
			# case 'DivisionSymbol':
			#	self.issues.append((symb, 'rational numbers are not supported'))


			# String and quoted identifier literals
			case 'StringSymbol' | 'QuotedIdentifierSymbol':
				op.type = SymbolInfo.Type.STRING
				op.props['isQid'] = name != 'StringSymbol'
				# info.theory = MaudeSymbolInfo.Theory.STRING

				# Define it a constructor of its kind
				self.kind_info[symb.getRangeSort().kind()].add_ctor(op)

			# Descent functions
			case 'MetaLevelOpSymbol':
				self.issues.append((symb, 'metalevel descent functions are not supported'))

			case 'CounterSymbol':
				self.issues.append((symb, 'counter symbol is not supported'))

			case 'LoopSymbol':
				self.issues.append((symb, 'loop operator treated as a regular operator'))
				op = self._index_regular_symbol(symb, (), False)

		return op

	def _compute_scc(self):
		"""Compute the SCCs and a pseudotopological ordering of kind dependencies"""

		analyzer = self

		class KindGraph(Mapping):
			def __iter__(self):
				return iter(analyzer.kind_info.values())

			def __getitem__(self, key):
				return key.depends

			def __len__(self):
				return len(analyzer.kind_info)

		self.kind_ordering, sccs = compute_scc(KindGraph())

		# Copy the connected components to the kinds
		for cc_id, cc_set in sccs.items():
			if len(cc_set) > 0:
				for kind_info in cc_set:
					kind_info.scc_number = cc_id
					kind_info.scc = cc_set

	def _analyze_kinds(self):
		"""Analyze kind dependencies"""

		# Scan the kind constructor for dependencies
		for kind_info in self.kind_info.values():
			for op in kind_info.ctors:
				symb = op.symb
				for k in range(symb.arity()):
					kind_info.depends.add(self.kind_info[symb.domainKind(k)])

		# Calculate SCC of kinds
		self._compute_scc()

		# Mark constructor arguments are recursive or not
		for kind_info in self.kind_info.values():
			# Non-recursive type
			if kind_info.scc is None:
				continue

			# Kind in the same strongly connected component
			rec_depends = {ki.kind for ki in kind_info.scc}

			for op in kind_info.ctors:
				symb = op.symb

				# Set the recursive args mark that will be useful for AST generation
				op.recursive_args = {k for k in range(symb.arity()) if symb.domainKind(k) in rec_depends}

	def _index_builtins(self):
		"""Look for defined symbols that are handled natively"""

		# Look for sort-membership tests
		for symb, info in self.symbol_info.items():
			if symb.getLineNumber() == '<automatic>':
				name = str(symb)
				if name.startswith('_::`'):
					sort = self.mod.findSort(name[4:])
					info.type = SymbolInfo.Type.SORT_TEST
					info.props['sort'] = self.get_sort_info(sort)

		# Set false to the same constructor as true
		if bool_info := self.kind_info.get(self.bool_kind):
			if len(bool_info.ctors) > 1:
				self.issues.append((bool_info.ctors[1].symb, f'Bool contains junk operator {bool_info.ctors[1].symb}'))
			bool_info.ctors_map[bool_info.props['false']] = bool_info.ctors_map[bool_info.props['true'].symb]

		# Check whether there are constructor in the Nat sort other than 0 and s
		if int_info := self.kind_info.get(self.int_kind):
			nat_sort = int_info.props['NatSort']

			for ctor in int_info.ctors:
				if ctor.type not in (SymbolInfo.Type.SUCCESSOR, SymbolInfo.Type.ZERO) and ctor.symb.getRangeSort() <= nat_sort:
					self.issues.append((ctor.symb, '{nat_sort} sort contains junk operator {ctor.symb}'))

		# Mark real special symbols
		for symb, info in self.symbol_info.items():
			kind = symb.getRangeSort().kind()

			if info.type == SymbolInfo.Type.SPECIAL:
				match kind:
					case self.bool_kind:
						info.theory = SymbolInfo.Theory.BOOL

					case self.int_kind:
						info.theory = SymbolInfo.Theory.INTEGRAL

					case self.float_kind:
						info.theory = SymbolInfo.Theory.FLOAT

		for symb, info in self.symbol_info.items():
			# Bool pseudospecials: this is not safe against renamings
			# and a general exception mechanism would be useful
			if symb.getRangeSort().kind() == self.bool_kind and str(symb) in BOOL_PSEUDOSPECIALS:
				info.theory = SymbolInfo.Theory.BOOL
				info.top_equations.clear()
				info.nested_equations.clear()

		# Check whether kind elements should be wrapped
		NOT_WRAPPED = (SymbolInfo.Type.SUCCESSOR, SymbolInfo.Type.BOOLEAN_LITERAL, SymbolInfo.Type.FLOAT_LITERAL)

		for kind_info in self.kind_info.values():
			if len(kind_info.ctors) == 1 and kind_info.ctors[0].type in NOT_WRAPPED:
				kind_info.wrapped = False

	def _index_assoc(self):
		"""Index associative symbols"""

		for op in self.symbol_info.values():
			if op.symb.hasAttr(maude.OP_ASSOC):
				# Make the identity associative too
				if identity := op.props.get('identity'):
					self.symbol_info[identity.symbol()].theory = SymbolInfo.Theory.ASSOC

				# Find the sorts containing the associative operator
				list_sorts = {list(decl.getDomainAndRange())[-1] for decl in op.symb.getOpDeclarations()}
				op.props['list_sorts'] = list_sorts

				# Find out whether the list can nested in other constructors of the kind
				if op.ctor:
					kind = op.symb.getRangeSort().kind()
					kind_info = self.kind_info[kind]

					# Mark these sorts as associative
					for sort in list_sorts:
						kind_info.sorts[str(sort)].theory = SymbolInfo.Theory.ASSOC

					if not any(sort <= s for ctor in kind_info.ctors if ctor != op for s in ctor.sort_signature[:-1] for sort in list_sorts):
						kind_info.props['list_sorts'] = list_sorts
						kind_info.hidden_ctors.add(kind_info.ctors_map[op.symb])

						# Check whether the next sort is a whole-kind sort without this one
						candidate = None

						for k in range(1, kind.nrSorts()):
							sort = kind.sort(k)

							if candidate is None:
								if sort not in list_sorts:
									candidate = sort

							else:
								if not (sort <= candidate):
									candidate = None
									break

						if candidate:
							kind_info.sorts[str(candidate)].whole_sort = True

	def _index_equations(self):
		"""Index equations by symbol"""

		# Track equations for symbol occurrence
		equation_occurs = defaultdict(list)

		for eq in self.mod.getEquations():
			if eq.isNonexec():
				continue

			for symb, top in collect_symbols(eq.getLhs()):
				equation_occurs[symb].append((eq, top))

		return equation_occurs

	def _index_regular_symbol(self, symb, equations, is_ctor):
		"""Index regular (i.e. non-special) symbol"""

		info = SymbolInfo(symb, SymbolInfo.Type.NORMAL)
		info.ctor = is_ctor

		# Identity symbol for the U axiom (if any)
		identity_symb = None

		# Find out its theory
		if symb.hasAttr(maude.OP_ASSOC):
			# Check the identity element (which can be arbitrary terms)
			if identity_element := symb.getIdentity():
				identity_symb = identity_element.symbol()

				# Some identity elements are constants and are easy to handle
				if identity_symb.arity() != 0 or not _is_ctor(identity_symb):
					identity_symb = None

			info.props['identity'] = identity_element

			if symb.hasAttr(maude.OP_COMM):
				info.theory = SymbolInfo.Theory.ACU
			else:
				info.theory = SymbolInfo.Theory.ASSOC

		elif symb.hasAttr(maude.OP_COMM):
			info.theory = SymbolInfo.Theory.COMM

		elif symb.hasAttr(maude.OP_ITER):
			info.theory = SymbolInfo.Theory.ITER

		# Find equations involving this symbol
		if eqs := equations.get(symb):
			for eq, top in eqs:
				if top:
					info.top_equations.append(EquationInfo(eq))
				else:
					info.nested_equations.append(eq)

		# Sort top equations by otherwise-ness
		info.top_equations.sort(key=lambda e: e.eq.isOwise())

		# Add the constructor to the kind structure
		if is_ctor:
			kind_info = self.kind_info[symb.getRangeSort().kind()]

			# If the identity element is already registered
			if identity_symb is not None:
				if (index := kind_info.ctors_map.get(identity_symb)) is not None:
					kind_info.ctors[index] = info
					kind_info.ctors_map[info.symb] = index
				else:
					# We assume add_ctor will give it the next index
					kind_info.ctors_map[identity_symb] = len(kind_info.ctors)
					kind_info.add_ctor(info)
			else:
				kind_info.add_ctor(info)

		elif not eqs:
			self.issues.append((symb, 'non-constructor symbol without equations'))

		return info

	def _index_symbol(self, symb: maude.Symbol, equations):
		"""Index operator symbols"""

		hooks = symb.getIdHooks()

		is_ctor = _is_ctor(symb)
		range_sort = symb.getRangeSort()

		# Fake symbol for variables
		if symb.arity() == 0 and str(symb).replace('`', '') == str(range_sort):
			info = SymbolInfo(symb, SymbolInfo.Type.VARIABLE)
			info.props['sort'] = range_sort

		# Regular symbol
		elif not hooks:
			info = self._index_regular_symbol(symb, equations, is_ctor)

		# Term with hooks
		else:
			if eqs := equations.get(symb):
				self.issues.append((symb, 'special symbol appears in left-hand side of equation', eqs[0][0]))

			if is_ctor:
				self.issues.append((symb, 'special symbol marked as constructor'))

			info = self._index_special(symb)

		self.symbol_info[symb] = info

	def get_symbol_info(self, symb: maude.Symbol):
		"""Get information about a symbol"""

		if info := self.symbol_info.get(symb):
			return info

		# We assume it is an unseen polymorph
		self._index_symbol(symb, {})

		return self.symbol_info[symb]

	def _analyze_sorts(self):
		"""Analyze sorts"""

		for kind_info in self.kind_info.values():
			# Make a explicit list of subsorts
			for sort_info in kind_info.sorts.values():
				sort_info.subsorts = [kind_info.sorts[str(sort)] for sort in sort_info.sort.getSubsorts()]

			# Gather constructors with that range
			for op in kind_info.ctors:
				sort = op.symb.getRangeSort()
				kind_info.sorts[str(sort)].ctors.append(op)

		# Link sorts with its membership axioms
		for mb in self.mod.getMembershipAxioms():
			if mb.isNonexec():
				continue

			kind_info = self.kind_info[mb.getSort().kind()]
			sort_info = kind_info.sorts[str(mb.getSort())]

			sort_info.memberships.append(mb)

		# Calculate signatures at the sort level
		for symb, op in self.symbol_info.items():
			decls = list(symb.getOpDeclarations())

			# No declaration at all
			if not decls:
				continue

			# A single declaration
			elif len(decls) == 1:
				op.sort_signature = tuple(decls[0].getDomainAndRange())

			# Multiple declarations
			else:
				signature = [self.kind_info[s.kind()].sort_indices[s] for s in decls[0].getDomainAndRange()]
				kinds = [self.kind_info[s.kind()] for s in decls[0].getDomainAndRange()]

				for decl in decls[1:]:
					for k, (sort, kind_info) in enumerate(zip(decl.getDomainAndRange(), kinds)):
						kind = kind_info.kind

						index = min(signature[k], kind_info.sort_indices[sort])
						old_sort = kind.sort(signature[k])

						while not (kind.sort(index) >= old_sort and kind.sort(index) >= sort):
							index -= 1

						signature[k] = index

				op.sort_signature = tuple(kind.kind.sort(index) for index, kind in zip(signature, kinds))

	def _index_rules(self):
		"""Index rules per kind"""

		# Collect rules
		for rl in self.mod.getRules():
			if rl.isNonexec():
				continue

			kind_info = self.kind_info[rl.getLhs().getSort().kind()]
			kind_info.rules.append(EquationInfo(rl))

		# Mark direct rewritable kinds
		for kinfo in self.kind_info.values():
			if kinfo.rules:
				kinfo.rewritable = True

		# Calculate indirect rewritable kinds
		graph = {}

		for kinfo in self.kind_info.values():
			nested = set()

			for ctor in kinfo.ctors:
				frozen = set(ctor.symb.getFrozen())

				for k in range(ctor.symb.arity()):
					if k in frozen:
						continue

					nested.add(self.kind_info[ctor.symb.domainKind(k)])

			if nested:
				graph[kinfo] = nested

		def propagate_rewritable(graph, node):
			if not node.rewritable and any(child.rewritable for child in graph.get(node, ())):
					node.rewritable = True

		dfs(graph, propagate_rewritable)

	def _analyze_strategies(self):
		"""Analyze strategies"""

		# Index strategies
		for strat in self.mod.getStrategies():
			self.strat_info[strat] = StrategyInfo(strat)

		for sd in self.mod.getStrategyDefinitions():
			if sd.isNonexec():
				continue

			# TODO: process the definition
			self.strat_info[sd.getStrategy()].append(sd)

	def _compute_cfg(self):
		"""Compute a control flow graph for the module"""

		analyzer = self

		class CallGraph(Mapping):
			def __iter__(self):
				return iter(analyzer.symbol_info.values())

			def __getitem__(self, key):
				symbols = {symb for eq in key.top_equations for symb in eq.called_symbols()}
				return {analyzer.symbol_info[symb] for symb in symbols}

			def __len__(self):
				return len(analyzer.symbol_info)

		self.symbol_ordering, sccs = compute_scc(CallGraph())

		# Copy the connected components to the symbols
		for cc_id, cc_set in sccs.items():
			if len(cc_set) > 1:
				for symbol_info in cc_set:
					symbol_info.scc_number = cc_id
					symbol_info.scc = cc_set

	def _analyze(self):
		"""Analyze module components for compilation"""

		# Initially the kind info map
		self.kind_info = {kind: KindInfo(kind) for kind in self.mod.getKinds()}

		# Index equations by symbol
		equations = self._index_equations()

		# Scan symbols
		for symb in self.mod.getSymbols():
			self._index_symbol(symb, equations)

		# Compute a control flow graph
		self._compute_cfg()
		# Analyze sorts
		self._analyze_sorts()
		# Look for defined symbols that are handled natively
		self._index_builtins()
		# Analyze operators with axioms
		self._index_assoc()
		# Analyze kind dependencies
		self._analyze_kinds()
		# Analyze rules
		self._index_rules()
		# Analyze strategies
		self._analyze_strategies()

	def get_sort_info(self, sort: maude.Sort):
		"""Get the information about a sort"""

		return self.kind_info[sort.kind()].sorts[str(sort)]

	def dump(self):
		"""Dump a summary of the collected information"""

		return {
			'module': str(self.mod),
			'kinds': {kind.name: kind.dump() for kind in self.kind_info.values()},
			'ops': [op.dump() for op in self.symbol_info.values()],
		}
