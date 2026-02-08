#
# Python code generator
#

import importlib.resources
import io
import json
import sys

from .common import process_builtins
from .. import ast
from ..analyzer import KindInfo
from ..dematch import dematch_or

# Translation table file (in the package resources)
TRANSLATION_TABLE = importlib.resources.files().parent / 'data' / 'python.json'


class PythonGenerator:
	"""Rust code generator"""

	def __init__(self, use_type_annot=True, out=sys.stdout):
		self.indent_level = 1  # current indentation level
		self.nested_else = False
		self.required_headers = set()
		self.use_type_annot = use_type_annot

		# Load builtins specification
		with TRANSLATION_TABLE.open() as jsf:
			spec = json.load(jsf)
			self.builtins = process_builtins(spec['builtins'])
			self.keywords = set(spec['keywords'])

		self.final_out = out
		self.out = io.StringIO()

	def supports_function_overloading(self):
		return False

	def _builtin_map(self, name: str):
		"""Map of builtin type name"""

		match name:
			case 'nat' | 'int':
				return 'int'
			case 'float':
				return 'float'
			case 'bool':
				return 'bool'
			case 'string':
				return 'str'

	def _variant_name(self, vtype: ast.NamedType, variant: ast.Variant):
		"""Get the name of a variant"""

		return f'{vtype.name}.{self.clean_identifier(variant.name.title())}'

	def _generate_pattern(self, pattern: ast.Pattern, out):
		"""Generate Python code for a pattern"""

		match pattern:
			case ast.ConstantPattern(cons):
				self.generate(cons, out)

			case ast.VariablePattern(var, used):
				if used:
					self.generate(var, out)
				else:
					out.write('_')

			case ast.ConstructorPattern(stype, variant, args):
				out.write(f'({self._variant_name(stype, variant)}, ')
				if args:
					for k, arg in enumerate(args):
						if k != 0:
							out.write(', ')
						self._generate_pattern(arg, out)

				out.write(')')

			case ast.TuplePattern(args):
				out.write('(')

				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self._generate_pattern(arg, out)

				out.write(')')

			case ast.SequencePattern(args):
				out.write('[')

				for k, (arg, ext) in enumerate(args):
					if k > 0:
						out.write(', ')
					if ext:
						out.write('*')
					self._generate_pattern(arg, out)

				out.write(']')

			case ast.OrPattern(args):
				start, *rest = args
				self._generate_pattern(start, out)

				# Python support or patterns (see PEP 636), but their
				# semantics is not the same as in Maude or Rust: the
				# condition will not be used to discard one of the variants.
				# Hence, or patterns should be removed from the AST if
				# they are guarded.

				for arg in rest:
					out.write(' | ')
					self._generate_pattern(arg, out)

	def _tweak_varname(self, name: str):
		"""Tweak the name of a variable to the Python convention"""

		return name

	def generate_block(self, block: list[ast.Node], out):
		"""Generate a block of Python statements"""

		for k, node in enumerate(block):
			# TODO: this should better be an AST-level transformation
			if k + 1 == len(block) and isinstance(node, ast.Expr):
				self.generate(ast.Return(node), out, stmt=True)
			else:
				self.generate(node, out, stmt=True)
				if len(block) > 1:
					out.write('\n')

	def generate(self, node: ast.Node, out, prec=1000, stmt=False):
		"""Generate Python code"""

		# Write the indent if it is no inside a expression
		indent = '\t' * self.indent_level

		if stmt:
			out.write(indent)

		match node:
			# Variables are translated literally
			case ast.Variable(name):
				out.write(self.clean_identifier(self._tweak_varname(name)))

			# No pointers in Python
			case ast.Dereference(expr) | ast.PointerCtor(expr):
				self.generate(expr, out)

			# Constants are almost translated literally
			case ast.Constant(value, _):
				out.write(repr(value))

			# Generates a variable declaration
			case ast.VarDecl(var, expr, _):
				# No need to declare variables
				if expr:

					out.write(f'{self.clean_identifier(var.name)}')

					if self.use_type_annot:
						out.write(f' : {self._translate_type(var.etype)}')

					out.write(' = ')
					self.generate(expr, out)
					out.write('\n')

			# Function calls are directly translated
			case ast.Call(fn, args, _, builtin):
				if not builtin:
					out.write(f'{self.clean_identifier(fn)}(')

					for k, expr in enumerate(args):
						if k != 0:
							out.write(', ')

						self.generate(expr, out)

					out.write(')')

				# n-ary operation
				else:
					pattern, op_prec, deps = self.builtins.get(fn, (None,) * 3)

					if pattern is None:
						raise ValueError(f'unsupported operator: {fn}')

					# Add header requirements
					self.required_headers.update(deps)

					# Precedence of this operation
					next_prec = op_prec if op_prec > 0 else 1000

					if op_prec >= prec:
						out.write('(')

					for token in pattern:
						if isinstance(token, int):
							self.generate(args[token], out, prec=next_prec)
						else:
							out.write(token)

					if op_prec >= prec:
						out.write(')')

			case ast.Return(expr):
				# Dirty fix
				if isinstance(expr, ast.MatchExpr):
					self.generate(expr, out)
				else:
					out.write('return ')
					self.generate(expr, out)
					out.write('\n')

			case ast.IfExpr(test, body, orelse):
				out.write('(')
				self.generate(body, out,  prec=16)
				out.write(' if ')
				self.generate(test, out, prec=16)
				out.write(' else ')
				self.generate(orelse, out, prec=16)
				out.write(')')

			case ast.If(test, body, orelse):
				# Condition
				initial_level = self.indent_level

				out.write('if ')
				self.nested_else = False

				# Deal with the special case of the conditional with option
				if isinstance(node, ast.IfOptional):
					if node.capture:
						out.write('(')
						self.generate(node.capture, out)
						out.write(' := ')
						self.generate(test, out)
						out.write(')')
					else:
						self.generate(test, out)

					out.write(' is None' if not body and orelse else ' is not None')
					if not body and orelse:
						body, orelse = orelse, body
				else:
					self.generate(test, out)

				out.write(':\n')
				self.indent_level += 1

				# Positive branch
				self.generate_block(body, out)
				out.write('\n')

				# Negative branch
				if orelse:
					out.write(f'{indent}else:\n')
					self.generate_block(orelse, out)

				self.indent_level = initial_level

			# Match expression
			case ast.MatchStmt(expr, _):

				# Remove disjunctive patterns (if any)
				cases = dematch_or(node).cases

				out.write('match ')
				self.generate(expr, out)
				out.write(':\n')

				self.indent_level += 2

				for case in cases:
					out.write(f'{indent}\tcase ')

					# Generate the matching pattern
					self._generate_pattern(case.pattern, out)

					# Introduce a condition (if any)
					if case.condition and not isinstance(case.condition, ast.Constant):
						out.write(' if ')
						self.generate(case.condition, out)

					out.write(':\n')

					self.generate_block(case.body, out)
					out.write('\n')

				self.indent_level -= 2

			# Tuple or sequence
			case ast.Tuple(args):
				out.write('(')
				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self.generate(arg, out)
				out.write(')')

			# Tuple or sequence
			case ast.Sequence(args):
				out.write('[')
				for k, (arg, ext) in enumerate(args):
					if k != 0:
						out.write(', ')
					if ext:
						out.write('*')
					self.generate(arg, out)
				out.write(']')

			# Optional value
			case ast.OptionalValue(arg):
				if arg is not None:
					self.generate(arg, out)
				else:
					out.write('None')

			# The selected variant is checked with the variant interface
			case ast.VariantTest(vtype, variant, expr):
				self.generate(expr, out)
				out.write(f'[0] == {self._variant_name(vtype, variant)}')

			# Access to a field of a variant
			case ast.VariantAccess(_, _, index, expr):
				self.generate(expr, out)
				out.write(f'[{index + 1}]')

			# Random access to tuple
			case ast.TupleAccess(expr, index):
				self.generate(expr, out)
				out.write(f'[{index}]')

			# Random sequence to sequence
			case ast.SequenceAccess(expr, index):
				self.generate(expr, out)
				out.write('[')
				self.generate(index, out)
				out.write(']')

			# Sequence length
			case ast.SequenceLength(expr):
				out.write('len(')
				self.generate(expr, out)
				out.write(')')

			case ast.Constructor(vtype, variant, args):
				out.write(f'({self._variant_name(vtype, variant)}, ')
				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self.generate(arg, out)
				out.write(')')

	def clean_identifier(self, ident: str):
		"""Clean identifier to a valid Python one"""

		# Simply prevents collapse with keywords
		if ident in self.keywords:
			return f'{ident}_'

		# Avoid generating identifiers surrounded by underscores
		if ident.startswith('_') and ident.endswith('_'):
			return f'{ident}i'

		return ident

	def generate_type(self, decl: ast.SumTypeDecl, info: KindInfo):
		"""Generate a type declaration for this kind (if needed)"""

		out = self.out

		# Variant types
		self.required_headers.add('enum')
		out.write(f'class {self.clean_identifier(decl.name)}(enum.Enum):\n')

		# Variant tags
		for vk, variant in enumerate(decl.variants):
			out.write(f'\t{self.clean_identifier(variant.name.title())} = {vk}  # ')

			if variant.types:
				for dk, dtype in enumerate(variant.types):
					if dk != 0:
						out.write(', ')
					out.write(self._translate_type(dtype))

			out.write('\n')

		out.write('\n\n')

	def _translate_type(self, dtype: ast.AstType):
		"""Translate a type to a Python type"""

		# Special types are translated
		match dtype:
			# Named type
			case ast.NamedType(name):
				if dtype.builtin:
					name = self._builtin_map(name)

				return name

			case ast.PointerType(arg):
				return self._translate_type(arg)

			case ast.SequenceType(arg):
				return f'[{self._translate_type(arg)}]'

			case ast.TupleType(args):
				return f'tuple[{", ".join(self._translate_type(arg) for arg in args)}]'

			case ast.OptionalType(arg):
				return f'{self._translate_type(arg)} | None'

	def _declare_function(self, decl: ast.FunctionDecl, out):
		"""Declare a function"""

		self.out.write(f'def {self.clean_identifier(decl.name)}(')

		not_first = False

		for atype, aname in decl.args:
			if not_first:
				out.write(', ')
			else:
				not_first = True

			self.out.write(f'{self._tweak_varname(aname)}: {self._translate_type(atype)}')

		self.out.write(f') -> {self._translate_type(decl.result_type)}')

	def generate_function(self, decl: ast.FunctionDecl, body: list[ast.Node]):
		"""Start the definition corresponding to a declaration"""

		self._declare_function(decl, self.out)

		self.out.write(':\n')

		# Generate target code
		self.generate_block(body, self.out)
		self.out.write('\n\n')

	def generate_prelude(self):
		"""Generate a prelude with the inclusions"""

		return '\n'.join(f'import {name}' for name in sorted(self.required_headers)) + '\n'

	def generate_tests(self, tests):
		"""Generate tests"""

		self.required_headers.add('unittest')
		out = self.out

		out.write('class MainTests(unittest.TestCase):\n')

		for k, (ttype, tid, *args) in enumerate(tests, start=1):
			out.write(f'\tdef test_{k}(self):\n\t\t"""{tid}"""\n\t\tself.assert')

			match ttype:
				case 'e':
					out.write('Equal(')
					self.generate(args[0], out)
					out.write(', ')
					self.generate(args[1], out)

				case 't' | 'f':
					out.write(f'{ttype == "t"}(')
					self.generate(args[0], out)

			out.write(')\n')

		out.write('\nif __name__ == \'__main__\':\n\tunittest.main()\n')

	def finish(self):
		"""Finish the code generation"""
		print(self.generate_prelude(), file=self.final_out)
		print(self.out.getvalue(), end='', file=self.final_out)
