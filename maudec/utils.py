#
# Utility functions
#

from collections.abc import Mapping, Iterable


def compute_scc[T](graph: Mapping[T, Iterable[T]]):
	"""Compute the SCCs and a pseudotopological ordering of a graph"""

	# This is a standard implementation of the Tarjan's SCC algorithm

	# Dictionary that assigns a SCC number to every state
	scc = {}
	next_index = 1
	# Postorder of the visit
	ordering = []

	# Initial state
	for initial in graph:
		# If the state is already visited
		if initial in scc:
			continue

		# The DFS stack and whether a state is in it
		stack = [(initial, iter(graph[initial]))]
		on_stack = {initial}
		scc[initial] = next_index
		next_index += 1

		while stack:
			state, child_it = stack[-1]

			# There are no more children, we are done with state
			if (child := next(child_it, None)) is None:
				on_stack.discard(state)
				stack.pop()

				# Propagate the SCC number to the parent
				if stack and scc[state] < scc[stack[-1][0]]:
					scc[stack[-1][0]] = scc[state]

				ordering.append(state)

				continue

			# The children is in the stack, we have found a cycle
			if child in on_stack:
				if scc[child] < scc[state]:
					scc[state] = scc[child]

			# We have not visited this state
			elif child not in scc:
				scc[child] = next_index
				next_index += 1
				on_stack.add(child)
				stack.append((child, iter(graph.get(child, ()))))

	# Collect the strongly connected components as sets
	scc_sets = {}

	for elem, cc_id in scc.items():
		scc_sets.setdefault(cc_id, set()).add(elem)

	# Reverse the postorder
	# ordering.reverse()

	return ordering, scc_sets


def dfs[T](graph: Mapping[T, Iterable[T]], after):
	"""Compute a DFS of a given graph"""

	stack, seen = [(key, None) for key in graph], set()

	while stack:
		node, child_iter = stack.pop()

		# Initially, we add node without children iterator
		if child_iter is None:
			if node in seen:
				continue

			seen.add(node)

			child_iter = iter(graph.get(node, ()))

		# Get the next children
		if (child := next(child_iter, None)) is None:
			after(graph, node)

		else:
			stack.append((node, child_iter))
			stack.append((child, None))


