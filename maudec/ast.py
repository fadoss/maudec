#
# AST as an intermediate step to generate code
#

from collections.abc import Sequence as SequenceT
from dataclasses import dataclass
from functools import cached_property

#
# Types in the AST

class AstType:
	"""Abstract base type for the target language"""

	pass


@dataclass(frozen=True)
class NamedType(AstType):
	"""Type with a name and no more structure"""

	name: str  # the name
	builtin: bool = False  # whether it is a builtin type


@dataclass
class TupleType(AstType):
	"""Tuple type"""

	args: SequenceT[AstType] = ()


@dataclass(frozen=True)
class PointerType(AstType):
	"""Pointer type"""

	arg: AstType


@dataclass
class SequenceType(AstType):
	"""Sequence (v.g. homogeneous list) type"""

	subtype: AstType  # type of its elements


@dataclass
class OptionalType(AstType):
	"""Optional type (it may have value or not)"""

	subtype: AstType


@dataclass
class Variant:
	"""Variant of a sum type"""

	name: str  # variant name
	types: SequenceT[AstType]  # variant type


@dataclass
class SumType(AstType):
	"""Sum type"""

	# name?
	variants: SequenceT[Variant]

#
# AST nodes

class Node:
	"""Generic node of the AST"""


class Expr(Node):
	"""Expression (a node that takes a value in the target language)"""

	# Every expression has a etype attribute or property,
	# but we do not declare it here


@dataclass
class Constant(Expr):
	"""Constant"""

	value: bool | str | int | float  # constant value
	etype: AstType  # its type


@dataclass(unsafe_hash=True)
class Variable(Expr):
	"""Variable"""

	name: str  # variable name
	etype: AstType  # its type


@dataclass
class VarDecl(Node):
	"""Variable declaration"""

	variable: Variable  # variable left-hand side
	expr: Expr | None  # initialization expression
	constant: bool = False


@dataclass
class Call(Expr):
	"""Call expression"""

	def __init__(self, fn, args, etype, builtin):
		super().__init__()
		self.fn = fn
		self.args = args
		self.etype = etype
		self.builtin = builtin

	fn: str
	args: SequenceT[Expr]
	etype: AstType
	builtin: bool = False


@dataclass
class If(Node):
	"""Conditional block"""

	test: Expr  # condition
	body: SequenceT[Node]  # positive branch
	orelse: SequenceT[Node] = ()  # negative branch


@dataclass
class IfExpr(Expr):
	"""Conditional expression"""

	test: Expr
	body: Expr
	orelse: Expr

	@cached_property
	def etype(self):
		return self.body.etype

@dataclass
class IfOptional(If):
	"""Conditional block on an optional value (common pattern)"""

	capture: Variable | None = None  # variable capture (if any)


@dataclass
class Dereference(Expr):
	"""Pointer dereference"""

	expr: Expr

	@cached_property
	def etype(self):
		return self.expr.etype.arg


@dataclass
class PointerCtor(Expr):
	"""Pointer creation"""

	expr: Expr

	@cached_property
	def etype(self):
		return PointerType(self.expr.etype)


class Pattern:
	"""Abstract pattern"""


@dataclass
class VariablePattern(Pattern):
	"""Pattern consisting of a variable"""

	name: Variable
	used: bool = True


@dataclass
class ConstantPattern(Pattern):
	"""Pattern consisting of a constant"""

	constant: Constant


@dataclass
class ConstructorPattern(Pattern):
	"""Pattern of a sum type constructor"""

	sum_type: AstType
	variant: Variant
	args: SequenceT[Pattern]


@dataclass
class OrPattern(Pattern):
	"""Disjunction of patterns (of the same class, with the same variables)"""

	args: SequenceT[Pattern]


@dataclass
class TuplePattern(Pattern):
	"""Pattern for a tuple"""

	args: SequenceT[Pattern]


@dataclass
class SequencePattern(Pattern):
	"""Pattern for a sequence"""

	args: SequenceT[tuple[Pattern, bool]]


@dataclass
class MatchBranch:
	"""Branch of a match construct"""

	pattern: Pattern
	condition: Expr | None
	body: SequenceT[Expr]


@dataclass
class MatchStmt(Node):
	"""Match statement (like expression, but without a value)"""

	expr: Expr
	cases: SequenceT[MatchBranch]


@dataclass
class MatchExpr(Expr, MatchStmt):
	"""Match expression"""

	@cached_property
	def etype(self):
		return self.cases[0].body[-1].etype


@dataclass
class Return(Node):
	"""Return statement"""

	expr: Expr  # return expression


@dataclass
class SumTypeDecl(Node):
	"""Sum type (tagged union) declaration"""

	# This is an AST node, not a type. Sum types only appear
	# in type expressions by their names

	name: str
	variants: SequenceT[Variant]


@dataclass
class VariantTest(Expr):
	"""Check whether a sum type belong to a specific variant"""

	vtype: NamedType  # sum type
	variant: Variant  # variant info
	expr: Expr  # expression of the sum type


@dataclass
class Constructor(Expr):
	"""Build a term of a sum type"""

	etype: NamedType  # sum type
	variant: Variant  # variant info
	args: SequenceT[Expr]  # initializer


@dataclass
class VariantAccess(Expr):
	"""Access the data associated to a variant"""

	vtype: NamedType  # sum type
	variant: Variant  # variant info
	index: int # field of the variant
	expr: Expr  # expression

	@cached_property
	def etype(self):
		return self.variant.types[self.index]


@dataclass
class TupleAccess(Expr):
	"""Access a component of tuple type"""

	expr: Expr  # expression of tuple type
	index: int  # index in the tuple

	@cached_property
	def etype(self):
		return self.expr.etype[self.index]


@dataclass
class Tuple(Expr):
	"""Tuple constructor"""

	args: SequenceT[Expr]

	@cached_property
	def etype(self):
		return TupleType(tuple(arg.etype for arg in self.args))


@dataclass
class Sequence(Expr):
	"""Sequence constructor"""

	args: SequenceT[tuple[Expr, bool]]

	@cached_property
	def etype(self):
		return SequenceType(self.args[0][0].etype if self.args else None)


@dataclass
class SequenceAccess(Expr):
	"""Access a component of sequence type"""

	expr: Expr   # expression of sequence type
	index: Expr  # index in the sequence

	@cached_property
	def etype(self):
		return self.expr.etype[index]


@dataclass
class SequenceLength(Expr):
	"""Get the length of a sequence"""

	expr: Expr  # expression of sequence type

	@cached_property
	def etype(self):
		return NamedType('int', builtin=True)


@dataclass
class SequenceSlice(Expr):
	"""Get a slice of a sequence"""

	expr: Expr   # expression of sequence type
	start: Expr  # first index in the subsequence
	end: Expr    # first index after the sequence

	@cached_property
	def etype(self):
		return self.expr.etype


@dataclass
class OptionalValue(Expr):
	"""Optional value"""

	def __init__(self, etype, expr=None):
		super().__init__()
		self.expr = expr
		self.etype = OptionalType(etype)

	expr: Expr | None  #
	etype: AstType


@dataclass
class FunctionDecl(Node):
	"""Function declaration"""

	name: str  # name of the function
	args: SequenceT[tuple[AstType, str]]
	result_type: AstType

#
# Visitors and transformers

class NodeVisitor:
	"""Visit all nodes in the AST tree"""

	def visit(self, node: Node):
		"""Visit a node"""

		handler = getattr(self, f'visit_{type(node).__name__}', self.generic_visit)
		handler(node)

	def generic_visit(self, node: Node):
		"""Visit a node (if nothing special is reserved for it)"""

		match node:
			case VarDecl() | Return() | VariantTest() | VariantAccess() | TupleAccess() | Dereference() | PointerCtor() | OptionalValue():
				if node.expr:
					self.visit(node.expr)

			case Call() | Tuple() | Constructor():
				for arg in node.args:
					self.visit(arg)

			case If():
				self.visit(node.test)
				for stmt in node.body:
					self.visit(stmt)
				for stmt in node.orelse:
					self.visit(stmt)

				if isinstance(node, IfOptional) and node.capture:
					self.visit(node.capture)

			case IfExpr():
				self.visit(node.test)
				self.visit(node.body)
				self.visit(node.orelse)

			case MatchStmt():
				self.visit(node.expr)

				for branch in node.cases:
					if branch.condition:
						self.visit(branch.condition)

					self.visit(branch.body)


class NodeTransformer:
	"""Transform the AST tree """

	def visit(self, node: Node):
		"""Visit a node"""

		handler = getattr(self, f'visit_{type(node).__name__}', self.generic_visit)

		return handler(node)

	def _visit_children(self, children: SequenceT[Node]):
		"""Visit a sequence of nodes"""

		results = tuple(self.visit(child) for child in children)

		# If none has changed, we keep the same term
		if all(child is result for result, child in zip(results, children)):
			return None

		return results

	def generic_visit(self, node: Node):
		"""Visit a node (if nothing special is reserved for it)"""

		match node:
			case VarDecl(var, expr, const):
				if expr and (result := self.visit(expr)) is not expr:
					return VarDecl(var, result, const)

			case Return(expr) | Dereference(expr) | PointerCtor(expr):
				if (result := self.visit(expr)) is not expr:
					return node.__class__(result)

			case OptionalValue(expr, etype):
				if (result := self.visit(expr)) is not expr:
					return OptionalValue(etype.subtype, result)

			case VariantTest(vtype, variant, expr):
				if (result := self.visit(expr)) is not expr:
					return VariantTest(vtype, variant, result)

			case VariantAccess(vtype, variant, index, expr):
				if (result := self.visit(expr)) is not expr:
					return VariantAccess(vtype, variant, index, result)

			case TupleAccess(expr, index):
				if (result := self.visit(expr)) is not expr:
					return TupleAccess(result, index)

			case Call(fn, args, etype, builtin):
				if results := self._visit_children(args):
					return Call(fn, results, etype, builtin)

			case Tuple(args):
				if results := self._visit_children(args):
					return Tuple(results)

			case Constructor(vtype, variant, args):
				if results := self._visit_children(args):
					return Constructor(vtype, variant, results)

			case If(test, body, orelse):
				new_test = self.visit(test)
				new_body = self._visit_children(body)
				new_orelse = self._visit_children(orelse)

				changed = new_test is not test or new_body is not None or new_orelse is not None

				if isinstance(node, IfOptional):
					new_capture = self.visit(node.capture) if node.capture else None

					if changed or new_capture:
						return IfOptional(new_test if new_test else test,
							          new_body if new_body else body,
							          new_orelse if new_orelse else orelse,
							          new_capture if new_capture else node.capture)

				else:
					if changed:
						return If(new_test if new_test else test,
							  new_body if new_body else body,
							  new_orelse if new_orelse else orelse)

			case IfExpr(test, body, orelse):
				if results := self._visit_children((test, body, orelse)):
					return IfExpr(*results)

			case MatchStmt():
				new_expr = self.visit(node.expr)

				new_cases = [None] * len(node.cases)

				for k, branch in enumerate(node.cases):
					new_condition = self.visit(branch.condition) if branch.condition else None
					new_body = self._visit_children(branch.body)

					if new_condition is not None or new_body is not None:
						new_cases[k] = MatchBranch(branch.pattern, new_condition or branch.condition, new_body or branch.body)


				if new_expr or any(c is not None for c in new_cases):
					new_cases = tuple((old if new is None else new) for old, new in zip(node.cases, new_cases))

					return node.__class__(new_expr or node.expr, new_cases)


		return node


#
# Auxiliary methods

def equals(lhs: Expr, rhs: Expr):
	"""Equality comparison"""

	return Call('==', (lhs, rhs), NamedType('bool', builtin=True), builtin=True)


def conjunction(args: SequenceT[Expr], default=None):
	"""Conjunction helper"""

	bool_type = NamedType('bool', builtin=True)

	if not args:
		return default

	if len(args) == 1:
		return args[0]

	return Call('&&', args, bool_type, builtin=True)
