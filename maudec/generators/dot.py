#
# AST generator in DOT format for debugging
#

import sys
import typing
from collections.abc import Sequence

from .. import ast
from ..analyzer import KindInfo

RECURSIVE_TYPES = (ast.Node, ast.Pattern, ast.MatchBranch)


class DotGenerator:
	"""Dot graph generator"""

	def __init__(self, use_variant=True, out=sys.stdout):
		self.use_variant = use_variant

		self.var_index = 0
		self.final_out = out
		self.out = out

	@staticmethod
	def _expandable(dtype):
		"""Check whether a type is expandable"""

		if isinstance(dtype, type):
			return issubclass(dtype, RECURSIVE_TYPES)

		if args := typing.get_args(dtype):
			arg = args[0] if isinstance(args[0], type) else typing.get_origin(args[0])
			return issubclass(arg, RECURSIVE_TYPES)

	def generate(self, node: ast.Node, out, parent='HEAD', label=''):
		"""Generate GraphViz's dot code"""

		# Current node identifier
		self.var_index += 1
		current = f'N{self.var_index}'

		# Make a link from the parent with optional label
		out.write(f'\t{parent} -> {current}{f' [label="{label}"]' if label else ""};\n')

		# Build the label of the AST node
		out.write(f'\t{current} [label="{node.__class__.__name__}(')

		is_first = True

		# Decide which arguments are expandable
		expandable = tuple(self._expandable(field.type) for field in node.__dataclass_fields__.values())

		# Non-expandable values
		for (name, data), expands in zip(node.__dataclass_fields__.items(), expandable):
			if expands:
				continue

			if is_first:
				is_first = False
			else:
				out.write(', ')

			value = getattr(node, name)

			if isinstance(value, ast.NamedType):
				value = value.name

			out.write(f'{name}={value}')

		out.write(')"];\n')

		# Node values
		for (name, data), expands in zip(node.__dataclass_fields__.items(), expandable):
			if not expands:
				continue

			# Value of the attribute
			value = getattr(node, name)

			# A sequence of nodes
			if typing.get_origin(data.type) == Sequence:
				for k, subfield in enumerate(value):
					self.generate(subfield, out, parent=current, label=f'{name}[{k}]')

			elif value:
				self.generate(value, out, parent=current, label=name)

	def generate_type(self, decl: ast.SumTypeDecl, info: KindInfo):
		"""Generate a type declaration for this kind (if needed)"""

		# Not interesting for the AST

	def _translate_type(self, dtype: ast.AstType, recursive: frozenset[str]=frozenset()):
		"""Translate a type name"""

		match dtype:
			case ast.NamedType(name):
				return name

			case ast.SequenceType(subtype):
				return f'[{self._translate_type(subtype)}]'

			case ast.TupleType(args):
				return f'({", ".join(self._translate_type(ty) for ty in args)})'

			case ast.OptionalType(subtype):
				return f'Optional<{self._translate_type(subtype)}>'

			case ast.PointerType(arg):
				return f'{self._translate_type(arg)}*'

			case _:
				raise ValueError(f'unknown type of type {type(dtype)}')


	def _declare_function(self, decl: ast.FunctionDecl, out):
		"""Declare a function"""

		out.write(f'\tHEAD [label="{decl.name}(')

		not_first = False

		for atype, aname in decl.args:
			if not_first:
				out.write(', ')
			else:
				not_first = True

			out.write(f'{aname}: {self._translate_type(atype)}')

		out.write(f') -> {self._translate_type(decl.result_type)}"];\n')

	def generate_function(self, decl: ast.FunctionDecl, body: list[ast.Node]):
		"""Start the definition corresponding to a declaration"""

		self.out.write(f'digraph {decl.name} {{\n')
		self._declare_function(decl, self.out)

		# Generate target code
		for stmt in body:
			self.generate(stmt, self.out, parent='HEAD')

		self.out.write('\n}\n\n')

	def generate_prelude(self):
		"""Generate a prelude with the inclusions"""

		# Nothing to do here

	def generate_tests(self, tests, test_ids):
		"""Generate tests"""

		# Nothing to do here

	def finish(self):
		"""Finish the code generation"""

		# Nothing to do here
