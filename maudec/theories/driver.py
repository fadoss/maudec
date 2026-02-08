#
# Generic compiler driver for a theory
#

from typing import Sequence

import maude

from .. import ast
from ..analyzer import SymbolInfo, KindInfo

SymbolType = SymbolInfo.Type

class Driver:
	"""Abstract compiler driver class"""

	def __init__(self, compiler: 'Compiler'):
		self.compiler = compiler

	def compile_expr(self, term: maude.Term, info: SymbolInfo, args: Sequence[ast.Expr]) -> ast.Expr | Sequence[ast.Expr] | None:
		"""Compile an expression"""

		return None

	def compile_pattern(self, arg: maude.Term, varmap: dict[maude.Term, ast.Expr], conditions: list[ast.Expr],
	                    context: maude.Sort | Sequence[maude.Sort]) -> ast.Pattern:
		"""Compile a pattern (the real one)"""

		raise NotImplementedError('compile_pattern is not implemented')

	def sort_test(self, lhs: ast.Expr, sort: maude.Sort) -> ast.Expr | None:
		"""Compile a sort membership test"""

		return None

	def handled(self, symb: maude.Symbol) -> bool:
		"""Whether the symbol is primitively handled"""

		return False

	def target_type(self, sort: maude.Sort, info: KindInfo) -> ast.AstType | None:
		"""Get the target type for the given sort (if any)"""

		return None  # no particular type
