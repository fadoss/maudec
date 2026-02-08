#
# Iterative operators
#

from .driver import Driver, maude, SymbolInfo, Sequence, ast

BOOL_TYPE = ast.NamedType('bool', builtin=True)
NAT_TYPE = ast.NamedType('nat', builtin=True)


class IterDriver(Driver):
	"""Compiler for iterative symbols"""

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):
		# We assume the input term is already normalized
		# and the symbol is a constructor

		# Check whether the argument can be an iter operator or not
		argument, = term.arguments()
		exponent = ast.Constant(term.getIterExponent(), NAT_TYPE)

		# If the argument is ground, we call the constructor
		if argument.ground():
			return args[0], exponent

		# Othewise, we call the normalization function
		return ast.Call(f'make_{self.compiler._function_name(info)}', (ast.PointerCtor(args[0]), exponent),
		                self.compiler._target_type(term.symbol().getRangeSort().kind()),
		                builtin=False)

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):
		"""Compile a pattern"""
		kind_info = self.compiler.analysis.kind_info[arg.getSort().kind()]

		kind_type = self.compiler._target_type(kind_info.kind)
		variant = self.compiler._get_variant(kind_info, arg.symbol())

		argument, = arg.arguments()
		variable = ast.Variable('n', NAT_TYPE)
		exponent = ast.Constant(arg.getIterExponent(), NAT_TYPE)

		# Require that the exponent is at least that of the pattern
		conditions.append(ast.Call('>=', (variable, exponent), BOOL_TYPE, builtin=True))

		return ast.ConstructorPattern(kind_type, variant, (
			self.compiler.compile_pattern(argument, varmap, conditions, context),
			ast.VariablePattern(variable),
		))
