#
# Transform the AST to remove match expressions
#

from itertools import product

from . import ast, diagnostics

# Boolean type
BOOL_TYPE = ast.NamedType('bool', builtin=True)
NAT_TYPE = ast.NamedType('nat', builtin=True)


def depattern(pattern: ast.Pattern, arg: ast.Expr, varmap: dict[ast.Variable, ast.Expr],
              conditions: list[ast.Expr]):
	"""Remove patterns"""

	match pattern:
		case ast.ConstantPattern(const):
			conditions.append(ast.equals(arg, const))

		case ast.VariablePattern(var, used) if used:
			varmap[var] = arg

		case ast.ConstructorPattern(vtype, variant, subpatterns):
			conditions.append(ast.VariantTest(vtype, variant, arg))

			for k, subpattern in enumerate(subpatterns):
				subarg = ast.VariantAccess(vtype, variant, k, arg)
				depattern(subpattern, subarg, varmap, conditions)

		case ast.OrPattern(args):
			raise ValueError("or patterns are not supported")

		case ast.TuplePattern(args):
			for k, subpattern in enumerate(args):
				subarg = ast.TupleAccess(arg, k)
				depattern(subpattern, subarg, varmap, conditions)

		case ast.SequencePattern(args):
			# Check whether the sequence pattern is translatable
			mult_indices = tuple(k for k, (_, mult) in enumerate(args) if mult)

			if len(mult_indices) > 1:
				diagnostics.warning('patterns with multiple subsequences are not supported, '
				                    'assuming that the additional subsequences are empty')

			conditions.append(ast.Call('>=' if mult_indices else '==', (
				ast.SequenceLength(arg),
				ast.Constant(len(args) - len(mult_indices) , NAT_TYPE)), BOOL_TYPE, builtin=True))

			for k, (subpattern, mult) in enumerate(args):
				# Suffix elements
				if not mult:
					index = None

					if mult_indices and k > mult_indices[0]:
						index = ast.Call('-', (
							ast.SequenceLength(arg),
							ast.Constant(len(args) - len(mult_indices) - k + 1, NAT_TYPE)), NAT_TYPE, builtin=True
						)
					else:
						index = ast.Constant(k, NAT_TYPE)
					subarg = ast.SequenceAccess(arg, index)
					depattern(subpattern, subarg, varmap, conditions)

				else:
					if not isinstance(subpattern, ast.VariablePattern):
						raise ValueError('pattern not allowed')

					if k == mult_indices[0]:
						# Index of the next position in the sequence
						end = ast.Call('-', (
							ast.SequenceLength(arg),
							# The number of elements after the current one,
							# taking the next subslices as empty
							ast.Constant(len(args) - len(mult_indices) - k, NAT_TYPE)),
							etype=NAT_TYPE, builtin=True,
						)

						varmap[subpattern.name] = ast.SequenceSlice(
							arg,
							ast.Constant(k, NAT_TYPE),
							end,
						)



class Substitute(ast.NodeTransformer):
	"""Substitute variables in the AST tree"""

	def __init__(self, varmap: dict[ast.Variable, ast.Expr]):
		self.varmap = varmap

	def visit_Variable(self, node: ast.Variable):
		"""Transform the variable value"""

		if value := self.varmap.get(node):
			return value

		return node


def dematch(mnode: ast.MatchStmt):
	"""Remove match expressions"""

	# First, eliminate disjunctive expressions
	mnode = dematch_or(mnode)

	# Body obtained as a result
	body = []

	# Input arguments
	arguments = mnode.expr.args if isinstance(mnode.expr, ast.Tuple) else (mnode.expr,)

	for branch in mnode.cases:
		patterns = branch.pattern.args if isinstance(branch.pattern, ast.TuplePattern) else (branch.pattern,)
		conditions, varmap = [], {}

		# Convert the pattern to a condition
		for pattern, arg in zip(patterns, arguments):
			depattern(pattern, arg, varmap, conditions)

		substitution = Substitute(varmap)

		# Include the original condition
		if branch.condition and not isinstance(branch.condition, ast.Constant):
			conditions.append(substitution.visit(branch.condition))

		# Use an if only if the condition is not trivial
		condition = ast.conjunction(conditions)
		inner_body = [substitution.visit(stmt) for stmt in branch.body]

		# If the condition is trivial, we omit it
		if condition is None:
			# If the last instruction is a expression, we introduce a return
			if isinstance(inner_body[-1], ast.Expr):
				inner_body[-1] = ast.Return(inner_body[-1])
			body.extend(inner_body)
		else:
			body.append(ast.If(condition, inner_body))

	return body


def denested_match(mnode: ast.MatchStmt, bound_vars=()):
	"""Remove nested patterns through pointers"""

	# Only constructor pattern need to be resolved
	if not isinstance(mnode.cases[0].pattern, ast.ConstructorPattern):
		return mnode

	# Get the greatest index of a bound variable
	index = max((int(va[4:]) for va in bound_vars if va.startswith('paux')), default=0) + 1

	# New branches
	new_branches = []

	for branch in mnode.cases:
		conditions, varmap, changed = [], {}, False

		# Skip non-constructor pattern
		if not isinstance(branch.pattern, ast.ConstructorPattern):
			new_branches.append(branch)
			continue

		# Convert the pattern
		subpatterns = list(branch.pattern.args)

		for k, subp in enumerate(subpatterns):
			variant_type = branch.pattern.variant.types[k]

			# Non-variable nested pattern through a pointer
			if isinstance(variant_type, ast.PointerType) and not isinstance(subp, ast.VariablePattern):
				# Variable to take the place of the nested pattern
				var_expr = ast.Variable(f'paux{index}', variant_type)
				subpatterns[k] = ast.VariablePattern(var_expr)
				index += 1
				changed = True

				# Turn the pattern into conditions
				depattern(subp, ast.Dereference(var_expr), varmap, conditions)

		# Keep the branch if not changed
		if not changed:
			new_branches.append(branch)
			continue

		substitution = Substitute(varmap)

		# Add the new condition
		if branch.condition:
			conditions.append(substitution.visit(branch.condition))

		# Use an if only if the condition is not trivial
		condition = ast.conjunction(conditions)
		inner_body = [substitution.visit(stmt) for stmt in branch.body]

		new_branches.append(
			ast.MatchBranch(ast.ConstructorPattern(branch.pattern.sum_type, branch.pattern.variant, subpatterns),
			                condition, inner_body))

	return mnode.__class__(mnode.expr, new_branches)


def split_or_pattern(pat: ast.Pattern):
	"""Split a disjunctive pattern"""

	match pat:
		case ast.ConstructorPattern() | ast.TuplePattern():
			# Take disjunctions to the top level
			split_args = [split_or_pattern(arg) for arg in pat.args]

			# No need for recomposition
			if all(len(arg) == 1 for arg in split_args):
				return pat,

			make_pattern = ast.TuplePattern

			if isinstance(pat, ast.ConstructorPattern):
				make_pattern = lambda e: ast.ConstructorPattern(pat.sum_type, pat.variant, e)

			return tuple(make_pattern(instance) for instance in product(split_args))

		case ast.OrPattern(args):
			# Split the disjunctive pattern recursively
			return (p for arg in args for p in split_or_pattern(arg))

	return pat,


def dematch_or(mnode: ast.MatchStmt, only_guarded=False):
	"""Remove disjunctive pattern in the first level"""

	new_cases = []

	for case in mnode.cases:
		# Split cases
		if not only_guarded or case.condition is not None:
			for pat in split_or_pattern(case.pattern):
				new_cases.append(ast.MatchBranch(pat, case.condition, case.body))

		# Keep the case
		else:
			new_cases.append(case)

	return mnode if len(new_cases) == len(mnode.cases) else mnode.__class__(mnode.expr, new_cases)
