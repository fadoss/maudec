#
# Commutative operators
#

from .driver import Driver, maude, SymbolInfo, Sequence, ast


class CommDriver(Driver):
	"""Driver for commutative symbols"""

	def __init__(self, compiler: 'Compiler'):
		super().__init__(compiler)

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]):
		"""Normalize the terms"""

		# We do not normalize non-constructor terms
		# (normalization is only useful for testing equality)
		if not info.ctor:
			return args

		# C operators are required to have two arguments
		fst, snd = term.arguments()

		# If the arguments are ground, we order them statically
		if fst.ground() and snd.ground():
			# TODO: The next version of the Maude library will expose term order,
			# but now we do the following, which may not be always correct
			if snd.prettyPrint(0) < fst.prettyPrint(0):
				args = tuple(reversed(args))

			return args

		# Othewise, we call the normalization function
		return ast.Call(f'make_{self.compiler._function_name(info)}', args,
		                self.compiler._target_type(term.symbol().getRangeSort().kind()),
		                builtin=False)

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort], topmost=False):
		"""Compile a pattern"""
		kind_info = self.compiler.analysis.kind_info[arg.getSort().kind()]
		variant_name = self.compiler._variant_name(arg.symbol())
		kind_type = self.compiler._target_type(kind_info.kind)

		# Contexts to compile the subpatterns
		context = self.compiler.analysis.symbol_info[arg.symbol()].sort_signature

		# Compile pattern for every argument
		subpatterns = [self.compiler.compile_pattern(subarg, varmap, conditions, subctx)
		               for subarg, subctx in zip(arg.arguments(), context)]

		# Pattern constructor
		make_pattern = (lambda args: ast.TuplePattern(args)) if topmost else (lambda args: ast.ConstructorPattern(kind_type, variant_name, args))

		# Optimization: if both sides are the same, we avoid a disjunctive pattern
		if subpatterns[0] == subpatterns[1]:
			return make_pattern(subpatterns)

		return ast.OrPattern((
			make_pattern(subpatterns),
			make_pattern(list(reversed(subpatterns))),
		))
