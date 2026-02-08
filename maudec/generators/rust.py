#
# Rust code generator
#

import importlib.resources
import io
import json
import sys
from typing import Sequence
import dataclasses

from .common import process_builtins
from .. import ast
from ..analyzer import KindInfo
from ..dematch import denested_match

# Translation table file (in the package resources)
TRANSLATION_TABLE = importlib.resources.files().parent / 'data' / 'rust.json'


@dataclasses.dataclass
class RustInfo:
	"""Information about Rust-specific concerns"""

	ref: bool = True
	owned: bool = False


class RustGenerator:
	"""Rust code generator"""

	# Value types (where cloning happens for free)
	VALUE_TYPES = frozenset(('int', 'nat', 'float', 'bool'))

	def __init__(self, use_variant=True, out=sys.stdout):
		self.indent_level = 1  # current indentation level
		self.nested_else = False
		self.use_variant = use_variant
		self.required_headers = set()

		# Load builtins specification
		with TRANSLATION_TABLE.open() as jsf:
			spec = json.load(jsf)
			self.builtins = process_builtins(spec['builtins'])
			self.keywords = set(spec['keywords'])

		self.final_out = out
		self.out = io.StringIO()

		# Information about the variables
		self.variables = []

	def supports_function_overloading(self):
		return False

	def _builtin_map(self, name: str, owned=True):
		"""Map of builtin type name"""

		match name:
			case 'nat' | 'int':
				return 'i32'
			case 'float':
				return 'f64'
			case 'bool':
				return 'bool'
			case 'string':
				return 'String' if owned else '&str'

	def _is_value_type(self, tp: ast.AstType):
		"""Whether a type is a value type"""

		return isinstance(tp, ast.NamedType) and tp.builtin and tp.name in self.VALUE_TYPES

	def _generate_pattern(self, pattern: ast.Pattern, out, owned=None):
		"""Generate Rust code for a pattern"""

		match pattern:
			case ast.ConstantPattern(cons):
				self.generate(cons, out)

			case ast.VariablePattern(var, used):
				if used:
					self.generate(var, out)
					if owned is not None:
						self.variables[-1][var.name] = owned
				else:
					out.write('_')

			case ast.ConstructorPattern(stype, variant, args):
				out.write(f'{stype.name}::{variant.name.title()}')
				if args:
					out.write('(')
					for k, arg in enumerate(args):
						if k != 0:
							out.write(', ')
						self._generate_pattern(arg, out, owned=owned)
					out.write(')')

			case ast.SequencePattern(args):
				out.write('[')

				# Only one sequence subpattern is admitted (-> complain)

				for k, (arg, seq) in enumerate(args):
					if k != 0:
						out.write(', ')

					if seq:
						if not isinstance(arg, ast.VariablePattern):
							raise ValueError('non-variable pattern in list')
						if arg.used:
							self._generate_pattern(arg, out, owned=RustInfo())
							out.write(' @ ')
						out.write('..')

					else:
						self._generate_pattern(arg, out, owned=owned)

				out.write(']')

			case ast.TuplePattern(args):
				out.write('(')

				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self._generate_pattern(arg, out, owned=owned)

				out.write(')')

			case ast.OrPattern(args):
				start, *rest = args
				self._generate_pattern(start, out, owned=owned)

				for arg in rest:
					out.write(' | ')
					self._generate_pattern(arg, out, owned=owned)

	def _tweak_varname(self, name: str):
		"""Tweak the name of a variable to the Rust convention"""

		# Rust complains if not camel case
		# (this is risky if Ab and ab exist)
		return f'{name[0].lower()}{name[1:]}'

	def _leaf_node(self, expr: ast.Node):
		"""Check whether the argument is a leaf node and whether it is an owned object"""

		match expr:
			case ast.Variable(name) | ast.Dereference(ast.Variable(name)):
				return True, self.variables[-1].get(name, RustInfo())

			case ast.Tuple(args) if args:
				return self._leaf_node(args[0])

			case ast.Call(builtin=False) | ast.Constructor() | ast.Sequence():
				return True, RustInfo(ref=False, owned=True)

		return False, RustInfo()

	def generate_block(self, block: Sequence[ast.Node], out, owned=None):
		"""Generate a block of Python statements"""

		for node in block:
			self.generate(node, out, stmt=True, owned=owned)
			if len(block) > 1:
				out.write('\n')

	def generate(self, node: ast.Node, out, prec=1000, stmt=False, owned=None):
		"""Generate Rust code"""

		# Write the indent if it is no inside a expression
		indent = '\t' * self.indent_level

		if stmt:
			out.write(indent)

		# Here we deal with borrowing and cloning
		needs_clone = False

		if owned is not None:
			is_leaf, owned_info = self._leaf_node(node)

			# Solve this at leaf nodes only
			if is_leaf:
				# If we expect an owned value
				if owned:
					if not owned_info.owned and not self._is_value_type(node.etype):
						needs_clone = True
						out.write('(')
					if owned_info.ref:
						out.write('*')

				elif not owned and not owned_info.ref:
					out.write('&')

		match node:
			# Variables are translated literally
			case ast.Variable(name):
				out.write(self.clean_identifier(self._tweak_varname(name)))

			# Pointer dereference
			case ast.Dereference(expr):
				_, owned_info = self._leaf_node(expr)
				# In order to dereference the pointer, we first need to
				# dereference the reference to the pointer (if any)
				out.write('&**' if owned_info.ref else '*')
				self.generate(expr, out, owned=False)

			# Make a smart pointer
			case ast.PointerCtor(expr):
				self.generate(expr, out, owned=True)
				out.write('.into()')

			# Constants are almost translated literally
			case ast.Constant(value, ctype):
				assert isinstance(ctype, ast.NamedType)

				if ctype.name == 'bool':
					out.write('true' if value else 'false')
				else:
					out.write(repr(value))

			# Generates a variable declaration
			case ast.VarDecl(var, expr, const):
				vtype = self._translate_type(var.etype)
				keyword = 'let' if const else 'let mut'

				out.write(f'{keyword} {self.clean_identifier(var.name)} : {vtype}')

				# Initialization is optional
				if expr:
					out.write(' = ')
					self.generate(expr, out)

					# This variable is owned because currently everything that
					# may initialize a variable is owned
					self.variables[-1][var.name] = RustInfo(ref=False, owned=True)

				out.write(';\n')

			# Function calls are directly translated
			case ast.Call(fn, args, _, builtin):
				if not builtin:
					out.write(f'{fn}(')

					for k, expr in enumerate(args):
						if k != 0:
							out.write(', ')

						self.generate(expr, out, owned=self._is_value_type(expr.etype))

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
							self.generate(args[token], out, prec=next_prec, owned=True)
						else:
							out.write(token)

					if op_prec >= prec:
						out.write(')')

			case ast.Return(expr):
				out.write('return ')
				self.generate(expr, out, owned=True)
				out.write(';\n')

			case ast.IfExpr(test, body, orelse):
				out.write('if ')
				self.generate(test, out, prec=16)
				out.write(' {')
				self.generate(body, out,  prec=16, owned=owned)
				out.write('} else {')
				self.generate(orelse, out, prec=16, owned=owned)
				out.write('}')

			case ast.If(test, body, orelse):
				# Condition
				initial_level = self.indent_level

				out.write('if ')
				self.nested_else = False
				# Deal with the builtin if-let construct
				new_scope = False
				if isinstance(node, ast.IfOptional):
					if node.capture:
						new_scope = True
						out.write('let Some(')
						self.generate(node.capture, out)
						out.write(') = ')
						self.generate(test, out)
						self.variables.append(dict(self.variables[-1]))
						self.variables[-1][node.capture.name] = RustInfo(ref=False, owned=True)
					else:
						self.generate(test, out)
						out.write('.is_none()' if not body and orelse else '.is_some()')
						if not body and orelse:
							body, orelse = orelse, body

				else:
					self.generate(test, out)

				out.write(' {\n')
				self.indent_level += 1

				# Positive branch
				self.generate_block(body, out, owned=owned)

				out.write(f'{indent}}}\n')

				if new_scope:
					self.variables.pop()

				# Negative branch
				if orelse:
					if len(orelse) == 1 and isinstance(orelse[0], ast.If):
						out.write(f'{indent}else ')
						self.nested_else = True
						self.indent_level -= 1
						self.generate(orelse[0], out, owned=owned)

					else:
						out.write(f'{indent}else {{\n')
						self.generate_block(orelse, out, owned=owned)
						out.write(f'{indent}}}\n')

				self.indent_level = initial_level

			# Match expression
			case ast.MatchStmt(expr, _):

				# Nested matches through pointers are not allowed
				cases = denested_match(node).cases

				out.write('match ')
				self.generate(expr, out)
				out.write(' {\n')

				# Whether the match expression is owned
				_, owned_info = self._leaf_node(expr)

				for case in cases:
					out.write('\t\t')

					# Open a new scope
					self.variables.append(dict(self.variables[-1]))

					# Generate the matching pattern
					self._generate_pattern(case.pattern, out, owned=owned_info)

					# Introduce a condition (if any)
					if case.condition and not (isinstance(case.condition, ast.Constant) and case.condition.value):
						out.write(' if ')
						self.generate(case.condition, out)

					out.write(' => ')

					if len(case.body) == 1:
						self.generate(case.body[0], out, owned=owned)
					else:
						out.write('{\n')
						self.indent_level += 2

						self.generate_block(case.body, out)

						self.indent_level -= 2
						out.write(indent + '\t}')

					out.write(',\n')

					# Close the scope
					self.variables.pop()

				# Simple optimization that may often work
				if cases[-1].condition is not None or not isinstance(cases[-1].pattern, ast.VariablePattern) and isinstance(node, ast.MatchExpr):
					# Since Rust only check exhaustiveness in simple cases
					out.write('\t\t_ => panic!("case not covered"),\n')

				out.write(f'{indent}}}')

			# Sequence
			case ast.Sequence(args):
				if not args:
					out.write('vec![]')

				elif len(args) == 1:
					if not args[0][1]:
						out.write('vec![')
					self.generate(args[0][0], out)
					if not args[0][1]:
						out.write(']')

				else:
					# Chained iterators
					for k, (arg, mult) in enumerate(args):
						if k != 0:
							out.write(').chain(')
						if not mult:
							out.write('std::iter::once(')

						self.generate(arg, out)

						if k == 0 and mult:
							out.write('.into_iter(')
						if k > 0 and not mult:
							out.write(')')
					out.write(').collect::<Vec<_>>()')

			# Tuple
			case ast.Tuple(args):
				out.write('(')
				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self.generate(arg, out)
				out.write(')')

			# Optional value
			case ast.OptionalValue(arg):
				if arg is not None:
					out.write('Some(')
					self.generate(arg, out)
					out.write(')')
				else:
					out.write('None')

			# The selected variant is checked with the variant interface
			case ast.VariantTest(vtype, variant, expr):
				out.write('matches!(')
				self.generate(expr, out)
				out.write(f', {vtype.name}::{variant.name.title()}(..))')

			# Access to a field of a variant
			case ast.VariantAccess(vtype, variant, index, expr):
				out.write('match ')
				self.generate(expr, out)
				pattern = ', '.join('_' if k != index else 'x' for k in range(len(variant.types)))
				out.write(f' {{ {vtype.name}::{variant.name.title()}({pattern}) => ')
				# Generate the variable
				self.variables.append({'x': self._leaf_node(expr)[1]})
				self.generate(ast.Variable('x', variant.types[index]), out, owned=owned)
				self.variables.pop()
				out.write(', _ => panic!("unchecked variant access") }')

			case ast.TupleAccess(expr, index):
				out.write('(')
				self.generate(expr, out)
				out.write(f').{index}')

			case ast.SequenceAccess(expr, index):
				self.generate(expr, out)
				out.write('[')
				self.generate(index, out)
				out.write(']')

			case ast.Constructor(vtype, variant, args):
				out.write(f'{vtype.name}::{variant.name.title()}')
				if args:
					out.write('(')
					for k, arg in enumerate(args):
						self.generate(arg, out, owned=True)
						if k + 1 != len(args):
							out.write(', ')
					out.write(')')

		# Close the clone
		if needs_clone:
			out.write(').clone()')

	def clean_identifier(self, ident: str):
		"""Clean identifier to a valid Rust one"""

		# Simply prevents collapse with keywords
		if ident in self.keywords:
			# TODO: this will not work in general, ident_ may already exist in this context
			return f'{ident}_'

		return ident

	def generate_type(self, decl: ast.SumTypeDecl, info: KindInfo):
		"""Generate a type declaration for this kind (if needed)"""

		out = self.out

		# Variant types
		# TODO: introduce derives as needed, not always
		out.write(f'#[derive(Clone, PartialEq, PartialOrd, Debug)]\nenum {self.clean_identifier(decl.name)} {{\n')

		# Variant tags
		for variant in decl.variants:
			out.write(f'\t{variant.name.title()}')

			if variant.types:
				out.write('(')

				for k, dtype in enumerate(variant.types):
					if k:
						out.write(', ')

					out.write(self._translate_type(dtype))

				out.write(')')

			out.write(',\n')

		out.write('}\n\n')

	def _translate_type(self, dtype: ast.AstType, owned=True):
		"""Translate a type to a Rust type"""

		# Special types are translated to their Rust types (identity, take care of string)
		match dtype:
			# Named type
			case ast.NamedType(name):
				if dtype.builtin:
					name = self._builtin_map(name, owned=owned)
				elif not owned:
					name = f'&{name}'

				return name

			case ast.PointerType(arg):
				# self.required_headers.add('std::boxed::Box')
				return f'Box<{self._translate_type(arg)}>'

			case ast.SequenceType(arg):
				if owned:
					return f'Vec<{self._translate_type(arg)}>'

				return f'&[{self._translate_type(arg)}]'

			case ast.TupleType(args):
				return f'({",".join(self._translate_type(arg) for arg in args)})'

			case ast.OptionalType(arg):
				return f'Option<{self._translate_type(arg)}>'

	def _declare_function(self, decl: ast.FunctionDecl, out):
		"""Declare a function"""

		self.out.write(f'fn {decl.name}(')

		not_first = False

		for atype, aname in decl.args:
			if not_first:
				out.write(', ')
			else:
				not_first = True

			self.out.write(f'{self._tweak_varname(aname)}: {self._translate_type(atype, owned=False)}')

			# If it is an argument of a basic type or a pointer, mark it as owned
			owned = self._is_value_type(atype) or isinstance(atype, ast.PointerType)
			self.variables[-1][aname] = RustInfo(ref=False, owned=True) if owned else RustInfo(ref=True, owned=False)

		self.out.write(f') -> {self._translate_type(decl.result_type)}')

	def generate_function(self, decl: ast.FunctionDecl, body: list[ast.Node]):
		"""Start the definition corresponding to a declaration"""

		self.variables.clear()
		self.variables.append({})

		self._declare_function(decl, self.out)

		self.out.write(' {\n')

		# Generate target code
		for stmt in body:
			self.generate(stmt, self.out, 0, stmt=True, owned=True)

		self.out.write('\n}\n\n')

	def generate_prelude(self):
		"""Generate a prelude with the inclusions"""

		return '\n'.join(f'use {name};' for name in sorted(self.required_headers)) + '\n'

	def generate_tests(self, tests):
		"""Generate tests"""

		out = self.out

		# Test module
		out.write('#[cfg(test)]\nmod tests {\n\tuse super::*;\n\n')

		for k, (ttype, tid, *args) in enumerate(tests):
			out.write(f'\t#[test]\n\tfn test_{k}() {{\n\t\tassert')

			match ttype:
				case 'e':
					out.write('_eq!(')
					self.generate(args[0], out)
					out.write(', ')
					self.generate(args[1], out)

				case 't' | 'f':
					out.write('!(' if ttype == 't' else '!(!(')
					self.generate(args[0], out)
					if ttype == 'f':
						out.write(')')

			out.write(f', "{tid.replace('{', '{{').replace('}', '}}')}");\n\t}}\n')

		out.write('}\n')

	def finish(self):
		"""Finish the code generation"""
		print(self.generate_prelude(), file=self.final_out)
		print(self.out.getvalue(), end='', file=self.final_out)
