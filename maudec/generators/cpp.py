#
# C++ code generator
#

import importlib.resources
import io
import json
import sys
from collections.abc import Sequence

from maudec import diagnostics

from .common import process_builtins
from .. import ast
from ..analyzer import KindInfo
from ..dematch import dematch

# Translation table file (in the package resources)
TRANSLATION_TABLE = importlib.resources.files().parent / 'data' / 'cpp.json'

# Extension of the shared_ptr class to implement deep comparisons for equality
# and order. This is cleaner than refining the comparator of the generated
# classes, which would force to explicit all cases and pointer arguments.

SMART_PTR_CODE = '''\
template <typename T>
class smart_ptr : public std::shared_ptr<T>
{
public:
	using std::shared_ptr<T>::shared_ptr;
	smart_ptr(std::shared_ptr<T>&& p) : std::shared_ptr<T>(p) {};
	smart_ptr(const std::shared_ptr<T>& p) : std::shared_ptr<T>(p) {};

	auto operator<=>(const smart_ptr<T>& other) const {
		T* left = this->get(), *right = other.get();

		if (left == right)
			return std::strong_ordering::equivalent;
		if (left != nullptr && right != nullptr)
			return *left <=> *right;

		return std::operator<=>(*this, other);
	}

	bool operator==(const smart_ptr<T>& other) const {
		return (*this <=> other) == 0;
	}

	bool operator<=(const smart_ptr<T>& other) const {
		return (*this <=> other) <= 0;
	}
};
'''

# Convert a std::vector to a std::span

VECTOR2SPAN = '''\
template <typename T>
std::span<T> to_span(std::vector<T>&& v)
{
	return std::span(v.begin(), v.end());
}
'''

class CppGenerator:
	"""C++ code generator"""

	# Value types (to avoid passing them by const reference)
	VALUE_TYPES = frozenset(('int', 'nat', 'float', 'bool'))

	def __init__(self, out=sys.stdout):
		self.indent_level = 1  # current indentation level
		self.nested_else = False

		# Header and fragments to be inserted at the beginning of the file
		self.required_headers = set()
		self.required_fragments = set()

		# Load builtins specification
		with TRANSLATION_TABLE.open() as jsf:
			spec = json.load(jsf)
			self.builtins = process_builtins(spec['builtins'])
			self.keywords = set(spec['keywords'])

		self.final_out = out
		self.out = io.StringIO()

	def _builtin_map(self, name: str):
		"""Map of builtin type name"""

		# nat could be converted to unsigned int,
		# but this may cause conversion problems
		return 'int' if name == 'nat' else name

	def _value_type(self, typ: ast.AstType):
		"""Whether a type is a value type"""

		return isinstance(typ, ast.NamedType) and typ.builtin and typ.name in self.VALUE_TYPES

	def generate(self, node: ast.Node, out, prec=1000, stmt=False):
		"""Generate C++ code"""

		indent = '\t' * self.indent_level

		# If a statement is expected, introduce a return
		if stmt and isinstance(node, ast.Expr) and not isinstance(node, ast.MatchExpr):
			return self.generate(ast.Return(node), out, stmt=False)

		match node:
			# Variables are translated literally
			case ast.Variable(name):
				out.write(self.clean_identifier(name))

			# Pointer dereference
			case ast.Dereference(value):
				out.write('*')
				self.generate(value, out, stmt=False)

			# Making a smart pointer
			case ast.PointerCtor(expr):
				out.write(f'new {expr.etype.name}(')
				self.generate(expr, out)
				out.write(')')

			# Constants are almost translated literally
			case ast.Constant(value, ctype):
				assert isinstance(ctype, ast.NamedType)

				if ctype.name == 'bool':
					out.write('true' if value else 'false')
				else:
					out.write(repr(value))

			# Tuple
			case ast.Tuple(args):
				out.write('{')
				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self.generate(arg, out)
				out.write('}')

			# Sequence
			case ast.Sequence(args):
				if not args:
					out.write('{}')

				else:
					self.required_headers.add('vector')
					self.required_fragments.add(VECTOR2SPAN)

					out.write('to_span(std::vector{')
					for k, (arg, ext) in enumerate(args):
						if k != 0:
							out.write(', ')
						if ext:
							diagnostics.warning('sequence composition is currently not supported for C++')
						self.generate(arg, out)
					out.write('})')

			# Optional values
			case ast.OptionalValue(expr):
				if expr is None:
					out.write('std::nullopt')
				else:
					out.write('std::make_optional(')
					self.generate(expr, out)
					out.write(')')

			# Generates a variable declaration
			case ast.VarDecl(var, expr):
				vtype = self._translate_type(var.etype)

				out.write(f'{indent}{vtype} {self.clean_identifier(var.name)}')

				# Initialization is optional
				if expr:
					out.write(' = ')
					self.generate(expr, out)

				out.write(';\n')

			# Function calls are directly translated
			case ast.Call(fn, args, _, builtin):
				# regular functions
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
				out.write(f'{indent}return ')
				self.generate(expr, out)
				out.write(';\n')

			case ast.IfExpr(test, body, orelse):
				self.generate(test, out, prec=16)
				out.write(' ? ')
				self.generate(body, out, prec=16)
				out.write(' : ')
				self.generate(orelse, out, prec=16)

			case ast.If(test, body, orelse):
				# Condition
				initial_level = self.indent_level

				out.write(f'{"" if self.nested_else else indent}if (')
				self.nested_else = False

				# Deal with the special case of the optional
				# (only needed for the value, because operator bool works out of the box)
				if isinstance(node, ast.IfOptional) and node.capture:
					out.write('auto opt_')
					self.generate(node.capture, out)
					out.write(' = ')

				self.generate(test, out)
				out.write(') {\n')


				self.indent_level += 1

				# Positive branch
				# Declare a variable for the optional value
				if isinstance(node, ast.IfOptional) and node.capture:
					out.write(f'{indent}\tauto {node.capture.name} = *opt_{node.capture.name};\n')

				for stmt in body:
					self.generate(stmt, out, stmt=True)

				out.write(f'{indent}}}\n')

				# Negative branch
				if orelse:
					if len(orelse) == 1 and isinstance(orelse[0], ast.If):
						out.write(f'{indent}else ')
						self.nested_else = True
						self.indent_level -= 1
						self.generate(orelse[0], out, stmt=True)

					else:
						out.write(f'{indent}else {{\n')
						for stmt in orelse:
							self.generate(stmt, out, stmt=True)
						out.write(f'{indent}}}\n')

				self.indent_level = initial_level

			# The selected variant is checked with the variant interface
			case ast.VariantTest(vtype, variant, expr):
				self.generate(expr, out)
				out.write('->' if isinstance(expr.etype, ast.PointerType) else '.')
				out.write(f'index() == {self._variant_name(vtype, variant)}')

			# Access also uses
			case ast.VariantAccess(vtype, variant, index, expr):
				# Variant not using a tuple
				with_tuple = len(variant.types) != 1

				if with_tuple:
					out.write(f'std::get<{index}>(')
				out.write(f'std::get<{self._variant_name(vtype, variant)}>(')
				if isinstance(expr.etype, ast.PointerType):
					out.write('*')
				self.generate(expr, out)
				out.write('))' if with_tuple else ')')

			case ast.TupleAccess(expr, index):
				out.write(f'std::get<{index}>(')
				self.generate(expr, out)
				out.write(')')

			case ast.SequenceAccess(expr, index):
				self.generate(expr, out)
				out.write('[')
				self.generate(index, out)
				out.write(']')

			case ast.SequenceLength(expr):
				self.generate(expr, out)
				out.write('.size()')

			case ast.SequenceSlice(expr, start, end):
				self.generate(expr, out)
				out.write('.subspan(')
				self.generate(start, out)
				out.write(', ')
				self.generate(end, out)
				out.write(' - ')
				self.generate(start, out)
				out.write(')')

			case ast.Constructor(vtype, variant, args):
				out.write(f'{vtype.name}(std::in_place_index<{self._variant_name(vtype, variant)}>')
				for arg in args:
					out.write(', ')
					self.generate(arg, out)
				out.write(')')

			case ast.MatchStmt():
				for block in dematch(node):
					self.generate(block, out)

	def _variant_name(self, vtype: ast.NamedType, variant: ast.Variant):
		"""Get the name of a variant"""

		return f'{vtype.name}::{variant.name.upper()}'

	def clean_identifier(self, ident: str):
		"""Clean identifier to a valid C++ one"""

		# Simply prevents collapse with keywords
		if ident in self.keywords:
			# TODO: this will not work in general, _ident may already exist in this context
			return f'_{ident}'

		return ident

	def generate_type(self, decl: ast.SumTypeDecl, info: KindInfo):
		"""Generate a type declaration for this kind (if needed)"""

		out = self.out
		self.required_headers.add('variant')

		# Variant types
		variant_types = []
		for variant in decl.variants:
			dtypes = variant.types

			# The support for each variant is either a tuple of
			# types or std::monostate (if there is none)
			if not dtypes:
				variant_types.append('std::monostate')
			else:
				variant_types.append(self._make_tuple_type(dtypes))

		# The type is represented as an extension of the variant type with
		# an enum to tag its arguments (for readability)

		out.write(f'struct {self.clean_identifier(decl.name)} : public std::variant')
		sep = ',\n\t'
		out.write(f'<\n\t{sep.join(variant_types)}\n> {{\n')

		# Inherit constructors
		out.write(f'\tusing std::variant<{", ".join(variant_types)}>::variant;\n')

		# Variant tags
		out.write('\n\tenum Variant {\n')

		for variant in decl.variants:
			out.write(f'\t\t{variant.name.upper()},\n')

		out.write('\t};\n};\n\n')

	def _make_tuple_type(self, types: Sequence[ast.AstType]):
		"""Translate sequence of types to a tuple type"""

		if len(types) == 1:
			return self._translate_type(types[0])

		self.required_headers.add('tuple')
		return f'std::tuple<{", ".join(self._translate_type(arg) for arg in types)}>'

	def _translate_type(self, dtype: ast.AstType, owned=False):
		"""Translate a type to C++ type"""

		# Special types are translated to their C++ types (identity, take care of string)
		match dtype:
			case ast.NamedType(name, builtin):
				return self._builtin_map(name) if builtin else name

			# Pointer type (for recursive data structures)
			case ast.PointerType(arg):
				self.required_headers.add('memory')
				self.required_headers.add('compare')
				self.required_fragments.add(SMART_PTR_CODE)
				return f'smart_ptr<{self._translate_type(arg, owned=True)}>'

			case ast.TupleType(args):
				self.required_headers.add('tuple')
				return self._make_tuple_type(args)

			case ast.SequenceType(arg):
				if owned:
					self.required_headers.add('vector')
					return f'std::vector<{self._translate_type(arg, owned=True)}>'
				else:
					self.required_headers.add('span')
					return f'std::span<{self._translate_type(arg, owned=True)}>'

			case ast.OptionalType(arg):
				self.required_headers.add('optional')
				return f'std::optional<{self._translate_type(arg, owned=owned)}>'

	def _declare_function(self, decl: ast.FunctionDecl, out):
		"""Declare a function"""

		self.out.write(f'{self._translate_type(decl.result_type, owned=True)} {self.clean_identifier(decl.name)}(')

		not_first = False

		for atype, aname in decl.args:
			if not_first:
				out.write(', ')
			else:
				not_first = True

			# We pass by const reference by default
			type_name = self._translate_type(atype)

			if not self._value_type(atype):
				type_name = f'const {type_name}&'

			self.out.write(f'{type_name} {aname}')

		self.out.write(')')

	def forward_declare_function(self, decl: ast.FunctionDecl):
		"""Make a forward declaration of a function"""

		self._declare_function(decl, self.out)
		self.out.write(';\n')

	def generate_function(self, decl: ast.FunctionDecl, body: list[ast.Node]):
		"""Start the definition corresponding to a declaration"""

		self._declare_function(decl, self.out)

		self.out.write(' {\n')

		# Generate target code
		for stmt in body:
			self.generate(stmt, self.out, stmt=True)

		self.out.write('}\n\n')

	def generate_prelude(self):
		"""Generate a prelude with the inclusions"""

		return '\n'.join(f'#include <{name}>' for name in sorted(self.required_headers)) + '\n\n' + \
			'\n'.join(sorted(self.required_fragments))

	def generate_tests(self, tests):
		"""Generate a main function to run some tests"""

		self.required_headers.add('iostream')
		out = self.out

		# No test framework is used, the conditions are simply
		# checked and message are shown if they fail

		out.write('int main() {\n\tbool ok = true;\n\n')

		for ttype, tid, *args in tests:
			out.write('\tif (')

			# Negation when testing true
			if ttype == 't':
				out.write('!(')

			self.generate(args[0], out)

			# Equality test
			if ttype == 'e':
				out.write(' != ')
				self.generate(args[1], out)

			elif ttype == 't':
				out.write(')')

			# A message is printed on error
			out.write(f') {{\n\t\tstd::cerr << "Test failed: \\"{tid}\\"\\n";\n\t\tok = false;\n\t}}\n')

		# The return code is 1 is something failed
		out.write('\n\treturn ok ? 0 : 1;\n}\n')

	def finish(self):
		"""Finish the code generation"""
		print(self.generate_prelude(), file=self.final_out)
		print(self.out.getvalue(), end='', file=self.final_out)
