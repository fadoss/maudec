#
# Builtin simple drivers (Booleans and floating-point numbers)
#

from .driver import *

BOOL_TYPE = ast.NamedType('bool', builtin=True)


class BoolDriver(Driver):
	"""Driver for Boolean values"""

	# Supported non-special operators
	# (they are defined with equations in Maude, but we translate them to built-in operators)
	SUPPORTED = {'_and_': '&&', '_or_': '||', '_xor_': '^', 'not_': '!', '_implies_': '=>'}

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):
		sym_name = str(term.symbol())

		match info.type:
			# true or false (as recorded in the property value)
			case SymbolType.BOOLEAN_LITERAL:
				return ast.Constant(info.props['value'], BOOL_TYPE)

			# One of the supported non-special operators above
			case _ if code := self.SUPPORTED.get(sym_name):
				return ast.Call(code, args, BOOL_TYPE, builtin=True)

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):
		"""Compile a pattern"""

		# The only constructor terms are the Boolean literals
		info = self.compiler.analysis.symbol_info[arg.symbol()]

		if info.type != SymbolType.BOOLEAN_LITERAL:
			raise ValueError('unexpected argument to compile_pattern for Booleans: not true/false')

		return ast.ConstantPattern(self.compile_expr(arg, info, ()))

	def handled(self, symb: maude.Symbol):
		return str(symb) in self.SUPPORTED


class FloatDriver(Driver):
	"""Driver for floating-point values"""

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):
		sym_name = str(term.symbol())

		# Floating-point literals
		if sym_name == '<Floats>':
			return ast.Constant(float(term), ast.NamedType('float', builtin=True))

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):
		"""Compile a pattern"""
		sym_name = str(arg.symbol())

		# The only constructor terms are the floating-point literals
		if sym_name != '<Floats>':
			raise ValueError(f'unexpected argument to compile_pattern for floating-point numbers: {arg}')

		op_info = self.compiler.analysis.symbol_info[arg.symbol()]

		return ast.ConstantPattern(self.compile_expr(arg, op_info, ()))
