#
# Rust code generator
#

import importlib.resources
import io
import json
import sys

from .common import process_builtins
from .. import ast
from ..analyzer import KindInfo
from ..dematch import denested_match, dematch_or, dematch
from queue import Queue


# Translation table file (in the package resources)
TRANSLATION_TABLE = importlib.resources.files().parent / 'data' / 'dafny.json'


class Call_substitute(ast.NodeTransformer):
	"""Substitute variables in the AST tree"""

	def __init__(self, out):
		self.out=out
		self.counter=0
		self.varmap= Queue()

	def generate_var(self, call: ast.Call):
		self.counter+=1
		return f'aux_{self.counter}'

	def visit_Call(self, node: ast.Call):
		"""Transform the call value"""
		if not node.builtin:
			var = self.generate_var(ast.Call)
			self.varmap.put((var, node))
			return ast.Variable(var, node.etype)
		return node
	def get_Vars(self):
		return self.varmap
		


class DafnyGenerator:
	"""Dafny code generator"""

	def __init__(self, use_variant=True, out=sys.stdout):
		self.indent_level = 1  # current indentation level
		self.nested_else = False
		self.use_variant = use_variant
		self.required_headers = set()
		self.output_base_case = ''
		self.transformer = Call_substitute(out)	


		# Load builtins specification
		with TRANSLATION_TABLE.open() as jsf:
			spec = json.load(jsf)
			self.builtins = process_builtins(spec['builtins'])
			self.keywords = set(spec['keywords'])

		self.final_out = out
		self.out = io.StringIO()
	def check_calls(self, transformer, node, out):
		match node:
			case ast.VarDecl(var, expr):
				aux_node = self.check_calls(transformer, expr, out)
				return transformer.visit(aux_node)
			case ast.Return(expr):
				aux_node = self.check_calls(transformer, expr, out)
				return transformer.visit(aux_node)
			case ast.Call(fn, args, aux, builtin):
				if not builtin:
					aux_args = []
					for k, expr in enumerate(args):
						aux_args.append(self.check_calls(transformer, expr, out))
						aux_args[k] =  transformer.visit(aux_args[k])
					
					return transformer.visit(ast.Call(fn, aux_args, aux, builtin))
					
				else:
					pattern, op_prec, deps = self.builtins.get(fn, (None,) * 3)
					new_args =list(args)
					for token in pattern:
						if isinstance(token, int):
							aux_node = self.check_calls(transformer, args[token], out)						
							new_args[token] = transformer.visit(aux_node)
					new_args = tuple(new_args)
					aux_node = transformer.visit(ast.Call(fn, new_args, aux, builtin))
					return aux_node

			case ast.IfExpr(test, body, orelse):
				aux_node = self.check_calls(transformer, test, out)
				return transformer.visit(aux_node)
			case ast.If(test, body, orelse):
				aux_node = self.check_calls(transformer, test, out)
				return transformer.visit(aux_node)
			case ast.MatchExpr(expr, _):
				aux_node = self.check_calls(transformer, expr, out)	
				return transformer.visit(aux_node)	
			case ast.Tuple(args):
				aux_node = []
				for k, arg in enumerate(args):
					aux_node.append(self.check_calls(transformer, arg, out))
					aux_node = transformer.visit(aux_node)
				return aux_node
			case ast.VariantTest(vtype, variant, expr):
				aux_node = self.check_calls(transformer, expr, out)
				return transformer.visit(aux_node)
			case ast.VariantAccess(vtype, variant, index, expr):
				aux_node = self.check_calls(transformer, expr, out)	
				return transformer.visit(aux_node)
			case ast.TupleAccess(expr, index):
				aux_node = self.check_calls(transformer, expr, out)	
				return transformer.visit(aux_node)	
			case ast.Constructor(vtype, variant, args):
				if args:
					aux_node = []
					for k, arg in enumerate(args):
						aux_node = self.check_calls(transformer, arg, out)
						aux_node =  transformer.visit(aux_node)
					return aux_node
		return node


	def swap_calls(self, stmt, out, indent):
		new_stmt = self.check_calls(self.transformer, stmt, out)
		varmap = self.transformer.get_Vars()
		while  not varmap.empty():
			(var, node) = varmap.get()
			out.write(f'{indent}var {var} := ')
			self.generate(node, out)
			out.write(f';\n ')
		return new_stmt


	def supports_function_overloading(self):
		return False

	def _builtin_map(self, name: str):
		"""Map of builtin type name"""

		return 'real' if name == 'float' else name


	def _is_value_type(self, tp: ast.AstType):
		"""Whether a type is a value type"""

		return isinstance(tp, ast.NamedType) and tp.builtin and tp.name in ('int', 'real', 'bool', 'nat')

	def _generate_pattern(self, pattern: ast.Pattern, out):
		"""Generate Dafny code for a pattern"""

		match pattern:
			case ast.ConstantPattern(cons):
				self.generate(cons, out)

			case ast.VariablePattern(var, used):
				if used:
					self.generate(var, out)
				else:
					out.write('_')

			case ast.ConstructorPattern(stype, variant, args):
				out.write(f'{variant.name}')
				if args:
					out.write('(')
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

			case ast.OrPattern(args):
				start, *rest = args
				self._generate_pattern(start, out)

				for arg in rest:
					out.write(' | ')
					self._generate_pattern(arg, out)

		return False		

	

	def generate(self, node: ast.Node, out, prec=1000, stmt=False):
		"""Generate Dafny code"""
		# Write the indent if it is no inside a expression
		indent = '\t' * self.indent_level
		if stmt:
			out.write(f'{indent}')


		match node:
			# Variables are translated literally
			case ast.Variable(name):
			
				out.write(self.clean_identifier(name))

						# Pointer dereference
			case ast.Dereference(expr):
				self.generate(expr, out)

			# Make a smart pointer
			case ast.PointerCtor(expr):
				self.generate(expr, out)

			# Constants are almost translated literally
			case ast.Constant(value, ctype):
				assert isinstance(ctype, ast.NamedType)
				if ctype.name == 'bool':
					out.write('true' if value else 'false')
				else:
					out.write(repr(value))
			# Generates a variable declaration
			case ast.SequenceSlice(expr, start, end):
				self.generate(expr, out)
				out.write('[')
				self.generate(start, out)
				out.write('.. ')
				self.generate(end, out)
				out.write(']')

			case ast.VarDecl(var, expr):
				vtype = self._translate_type(var.vtype)

				out.write(f'{indent} var {self.clean_identifier(var.name)} : ')

				# Initialization is optional
				if expr:
					out.write(f' {vtype} := ')
					self.generate(expr, out)

				out.write(';\n')
			case ast.Return(expr):
				out.write(f'{indent}output := ')
				self.generate(expr, out)
				out.write(';\n')
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

			case ast.IfExpr(test, body, orelse):
				out.write('if ')
				self.generate(test, out, prec=16)
				out.write('{')
				self.generate(body, out,  prec=16)
				out.write('} else {')
				self.generate(orelse, out, prec=16)
				out.write(f'{indent}}}')

			case ast.SequenceAccess(expr, index):
				self.generate(expr, out)
				out.write('[')
				self.generate(index, out)
				out.write(']')

			case ast.SequenceLength(expr):
				out.write('|')
				self.generate(expr, out)
				out.write('|')

			case ast.If(test, body, orelse):
				# Condition
				initial_level = self.indent_level

				out.write(f'{indent}if ')
				self.nested_else = False
				self.generate(test, out)
				out.write(' {\n')
				self.indent_level += 1

				# Positive branch
				for k, stmt in enumerate(body):
					new_stmt = self.swap_calls(stmt, out, indent)
					if k + 1 ==len(body):
						new_stmt=ast.Return(new_stmt)
					self.generate(new_stmt, out)

				out.write(f'{indent}}}\n')

				# Negative branch
				if orelse:
					if len(orelse) == 1 and isinstance(orelse[0], ast.If):
						out.write(f'{indent} }} else {{')
						self.nested_else = True
						self.indent_level -= 1						
						new_body = self.swap_calls(orelse[0], out)
						self.generate(ast.Return(new_body), out)
						

					else:
						
						out.write(f'{indent}}} else {{\n')
						for k, node in enumerate(orelse):
							new_body = self.swap_calls(orelse[0], out)
							if k + 1 == len(orelse) and isinstance(node, ast.Expr):
								self.generate(ast.Return(new_body), self.out, stmt=True)
							else:	
								self.generate(new_body, self.out, 0, stmt=True)

						out.write(f'{indent}}}\n')

				self.indent_level = initial_level

			# Match expression
			case ast.MatchExpr(expr, aux):

				if isinstance(expr, ast.SequenceType) or (isinstance(expr, ast.Variable) and isinstance(expr.etype, ast.SequenceType)):
					new_node=dematch(ast.MatchExpr(expr, aux))
					for block in new_node:
						self.generate(block, out)
				else:
					
					# Nested matches through pointers are not allowed
					cases = dematch_or(node).cases

					out.write('match ')
					self.generate(expr, out)
					out.write(' \n')

					for case in cases:
						out.write('\t\t case ')

						# Generate the matching pattern
						self._generate_pattern(case.pattern, out)
						out.write(' => ')

						# Introduce a condition (if any)
						if case.condition and not (isinstance(case.condition, ast.Constant) and case.condition.value):
							out.write(' if ')
							self.generate(case.condition, out)
							out.write(' {  ')

						if len(case.body) == 1:
							new_body = self.swap_calls(case.body[0], out, indent)
							self.generate(ast.Return(new_body), out)
						else:
							out.write('{\n')
							self.indent_level += 1
						# Generate target code
							for k, node in enumerate(case.body):
								new_body = self.swap_calls(block, out, indent)
								if k + 1 == len(node) and isinstance(node, ast.Expr):
									self.generate(ast.Return(new_body), self.out, stmt=True)
								else:	
									self.generate(new_body, self.out, 0, stmt=True)

							self.indent_level -= 1
							out.write(f'{indent}}}')
						if case.condition and not (isinstance(case.condition, ast.Constant) and case.condition.value):
							self.indent_level +=1
							out.write(f'{indent}}}')
						out.write('\n')


					out.write(f'{indent}')
		
			# Tuple
			case ast.Tuple(args):
				out.write('(')
				for k, arg in enumerate(args):
					if k != 0:
						out.write(', ')
					self.generate(arg, out)
				out.write(')')

			# The selected variant is checked with the variant interface
			case ast.VariantTest(vtype, variant, expr):
				out.write('matches!(')
				self.generate(expr, out)
				out.write(f', {vtype.name}::{variant.name}(..))')

			# Access to a field of a variant
			case ast.VariantAccess(vtype, variant, index, expr):
				out.write('match ')
				self.generate(expr, out)
				pattern = ', '.join('_' if k != index else 'x' for k in range(len(variant.types)))
				out.write(f' {{ {vtype.name}::{variant.name}({pattern}) => x')

			case ast.TupleAccess(expr, index):
				out.write('(')
				self.generate(expr, out)
				out.write(f').{index}')

			case ast.Constructor(vtype, variant, args):
				out.write(variant.name)
				if args:
					out.write('(')
					for k, arg in enumerate(args):
						self.generate(arg, out)
						if k + 1 != len(args):
							out.write(', ')
					out.write(')')

	def clean_identifier(self, ident: str):
		"""Clean identifier to a valid dafny one"""
		# Simply prevents collapse with keywords
		if ident in self.keywords:
			return f'{ident}_'

		if ident.startswith('_'):
			return f'i{ident}'

		return ident

		return ident
#TODO iden
	def generate_type(self, decl: ast.SumTypeDecl, info: KindInfo):
		"""Generate a type declaration for this kind (if needed)"""

		out = self.out

		# Variant types
		out.write(f'datatype {self.clean_identifier(decl.name)} =\n')

		# Variant tags
		for variant in decl.variants:
			out.write(f'\t| {variant.name}')

			if variant.types:
				out.write('(')

				for k, dtype in enumerate(variant.types):
					if k:
						out.write(', ')
#TODO arreglar el x:
					out.write(f'var_{k}: {self._translate_type(dtype)}')

				out.write(')')

			out.write('\n')

		out.write('\n\n')
#TODO
	def _translate_type(self, dtype: ast.AstType):
		"""Translate a type to a dafny type"""

		# Special types are translated to their dafny types (identity, take care of string)
		match dtype:
			# Named type
			case ast.NamedType(name):
				if dtype.builtin:
					name = self._builtin_map(name)

				return name

			case ast.PointerType(arg):
				# self.required_headers.add('std::boxed::Box')
				return f'{self._translate_type(arg)}'

			case ast.SequenceType(arg):

				return f'seq<{self._translate_type(arg)}>'

			case ast.TupleType(args):
				return f'({",".join(self._translate_type(arg) for arg in args)})'

	def _declare_function(self, decl: ast.FunctionDecl, out):
		"""Declare a function"""

		self.out.write(f'method {self.clean_identifier(decl.name)}(')

		not_first = False

		for atype, aname in decl.args:
			if not_first:
				out.write(', ')
			else:
				not_first = True

			self.out.write(f'{aname}: {self._translate_type(atype)}')


		self.out.write(f') returns (output : {self._translate_type(decl.result_type)})')

	def _tweak_varname(self, name: str):
		"""Tweak the name of a variable to the Rust convention"""

		# Rust complains if not camel case
		# (this is risky if Ab and ab exist)
		return f'{name[0].lower()}{name[1:]}'


	def generate_function(self, decl: ast.FunctionDecl, body: list[ast.Node]):
		"""Start the definition corresponding to a declaration"""

		self._declare_function(decl, self.out)

		self.out.write(' {\n')

		# Generate target code
		for k, node in enumerate(body):
			if k + 1 == len(body) and isinstance(node, ast.Expr) and not isinstance(node, ast.MatchExpr) and not isinstance(node, ast.IfExpr):
				self.generate(ast.Return(node), self.out, stmt=True)
			else:	
				self.generate(node, self.out, 0, stmt=True)

		self.out.write('\n}\n\n')

	def generate_prelude(self):
		"""Generate a prelude with the inclusions"""

		return '\n'.join(f'{name}' for name in sorted(self.required_headers)) + '\n'

	
	def generate_tests(self, tests):
		"""Generate tests"""

		out = self.out

		# Test module
		out.write('method test(){')

		for k, (ttype, tid, *args) in enumerate(tests):
			match ttype:
				case 'e':
					out.write('\n')
					aux_node0=self.swap_calls(args[0], out, '\t\t')
					aux_node1=self.swap_calls(args[1], out, '\t\t')
					out.write(f'\n\t\tvar a_{k} :=  ')
					self.generate(aux_node0, out)
					out.write(f';\n\t\tvar b_{k} := ')
					self.generate(aux_node1, out)
					out.write(f';\n\t\tassert a_{k} == b_{k};\n')
		out.write('\n}\n')

	def finish(self):
		"""Finish the code generation"""
		print(self.generate_prelude(), file=self.final_out)
		print(self.out.getvalue(), end='', file=self.final_out)
