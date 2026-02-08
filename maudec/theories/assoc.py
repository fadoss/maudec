#
# Associative operators
#

from .driver import Driver, maude, SymbolInfo, Sequence, ast, KindInfo


class AssocDriver(Driver):
	"""Compiler for associative symbols"""

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):
		# We assume the input term is already normalized

		kind_info = self.compiler.analysis.kind_info[term.getSort().kind()]
		op_info = kind_info.ctors[kind_info.ctors_map[term.symbol()]]
		list_sorts = op_info.props.get('list_sorts')

		# Terms headed by a constructor symbol (the list constructor) are translated to a sequence composition
		if info.ctor:
			return ast.Sequence(tuple((arg, sub.getSort() in list_sorts)
			                          for sub, arg in zip(term.arguments(), args)))

		# Non-constructor are handled as usual operators
		return args

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):
		"""Compile a pattern"""
		kind_info = self.compiler.analysis.kind_info[arg.getSort().kind()]
		op_info = kind_info.ctors[kind_info.ctors_map[arg.symbol()]]

		# Only a subset of restricted patterns is allowed
		# (those supported by Rust and Python)
		list_sorts = op_info.props.get('list_sorts', ())
		arguments = tuple((self.compiler.compile_pattern(sub, varmap, conditions, sub.getSort()), sub.getSort() in list_sorts)
		                  for sub in arg.arguments())

		return ast.SequencePattern(arguments)

	def target_type(self, sort: maude.Sort, info: KindInfo):
		"""Get the target type for the given sort"""

		# Whether it is a list sort
		if sort in info.props.get('list_sorts', ()):
			return ast.SequenceType(self.compiler._target_type(info.kind))
