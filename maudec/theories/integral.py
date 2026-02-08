#
# Integral theory (Nat and Int)
#

from .driver import Driver, ast, maude, Sequence, SymbolInfo, KindInfo, SymbolType

INT_TYPE = ast.NamedType('int', builtin=True)
NAT_TYPE = ast.NamedType('nat', builtin=True)
BOOL_TYPE = ast.NamedType('bool', builtin=True)


class IntegralDriver(Driver):
	"""Driver for Nat and Int"""

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):

		match info.type:
			case SymbolType.ZERO:
				return ast.Constant(0, NAT_TYPE)

			case SymbolType.SUCCESSOR:
				arg, = term.arguments()

				# Check whether the argument (and so the whole term) is a constant
				is_literal = self.compiler.analysis.symbol_info[arg.symbol()].type == SymbolType.ZERO

				if is_literal:
					return ast.Constant(term.getIterExponent(), NAT_TYPE)
				else:
					return ast.Call('NumberOpSymbol:+', (args[0], ast.Constant(term.getIterExponent(), NAT_TYPE)),
					                NAT_TYPE, builtin=True)

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):

		info = self.compiler.analysis.symbol_info[arg.symbol()]
		# Find a free variable name
		var_name = next(name for k in range(1, 10000) if (name := f'n_{k}') not in varmap)
		var_node = ast.Variable(var_name, INT_TYPE)

		match info.type:
			case SymbolType.ZERO:
				return ast.ConstantPattern(ast.Constant(0, NAT_TYPE))

			case SymbolType.SUCCESSOR:
				subarg, = arg.arguments()

				# Check whether the argument (and so the whole term) is a constant
				is_literal = self.compiler.analysis.symbol_info[subarg.symbol()].type == SymbolType.ZERO

				if is_literal:
					return ast.ConstantPattern(ast.Constant(arg.getIterExponent(), NAT_TYPE))

				if not subarg.isVariable():
					raise ValueError('argument of successor pattern is not a variable')

				# Value of the Maude variable with respect to the whole term
				var_expr = ast.Call('NumberOpSymbol:-',
				                    (var_node, ast.Constant(arg.getIterExponent(), NAT_TYPE)),
				                    INT_TYPE, builtin=True)

				# The variable should be at least the number of succesors
				var_term = subarg
				var_bound = ast.Call('NumberOpSymbol:>=',
				                     (var_node, ast.Constant(arg.getIterExponent(), NAT_TYPE)),
				                     BOOL_TYPE, builtin=True)

			case SymbolType.MINUS:
				subarg, = arg.arguments()

				# Pattern of the form - n
				if subarg.isVariable():
					var_expr = ast.Call('MinusSymbol:-', (var_node,), INT_TYPE, builtin=True)
					var_term = subarg
					var_bound = ast.Call('NumberOpSymbol:<',
					                     (var_node, ast.Constant(0, NAT_TYPE)),
					                     BOOL_TYPE, builtin=True)

				else:
					# Otherwise, the argument must a successor
					subinfo = self.compiler.analysis.symbol_info[subarg.symbol()]

					if subinfo.type != SymbolType.SUCCESSOR:
						# TODO: extensions (i.e. infinity) will not work
						raise ValueError(f'{subarg} is not a pattern for sort Int')

					subsubarg, = subarg.arguments()

					# Check whether the argument (and so the whole term) is a constant
					is_literal = self.compiler.analysis.symbol_info[subsubarg.symbol()].type == SymbolType.ZERO

					if is_literal:
						return ast.ConstantPattern(ast.Constant(-1, INT_TYPE))

					if not subsubarg.isVariable():
						raise ValueError('argument of negated successor pattern is not a variable')

					var_expr = ast.Call('-', (ast.Constant(- subarg.getIterExponent(), INT_TYPE), var_node),
					                    INT_TYPE, builtin=True)
					var_term = subsubarg
					var_bound = ast.Call('<=', (var_node, ast.Constant(- subarg.getIterExponent(), INT_TYPE)),
					                     BOOL_TYPE, builtin=True)

			case _:
				raise ValueError(f'unexpected symbol for integer pattern: {arg}')

		# If the variable does not exist
		if (other := varmap.get(var_term)) is None:
			varmap[var_term] = var_expr
			var_node.name = var_term.getVarName()
			conditions.append(var_bound)

		# If the variable is already bound
		else:
			conditions.append(ast.equals(other, var_expr))

		return ast.VariablePattern(var_node)

	def sort_test(self, lhs: ast.Expr, sort: maude.Sort):
		"""Make a sort test condition"""

		kind_info = self.compiler.analysis.kind_info[sort.kind()]
		zero = ast.Constant(0, NAT_TYPE)

		if sort == kind_info.props.get('NzNatSort'):
			return ast.Call('>', (lhs, zero), BOOL_TYPE, builtin=True)

		elif sort == kind_info.props.get('NatSort'):
			return ast.Call('>=', (lhs, zero), BOOL_TYPE, builtin=True)

		elif sort == kind_info.props.get('ZeroSort'):
			return ast.equals(lhs, zero)

	def target_type(self, sort: maude.Sort, info: KindInfo):
		"""Get the target type for the given sort"""

		# This needs some refinement, because if other constructors
		# have been defined on these sorts, we should not use them as is

		if (nat_sort := info.props.get('NatSort')) and sort <= nat_sort:
			return ast.NamedType('nat', builtin=True)

		if (int_sort := info.props.get('IntSort')) and sort <= int_sort:
			return ast.NamedType('int', builtin=True)
