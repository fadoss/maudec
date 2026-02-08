#
# Common resources for generators
#

import re

# Patterns may contain numbered placeholders for their arguments
PAT_REGEX = re.compile(r'(\{[0-9]*\})')


def process_builtins(ops: dict):
	"""Process the dictionary of builtins"""

	# The specification of builtins gives a format string for the code
	# of builtin operator in the target language. However, we do not
	# directly use it as a format string because the code generator
	# writes to a file instead of producing strings.

	for name, spec in ops.items():
		pattern, *extra = spec

		# Split the pattern by the placeholders, then replace
		# by the argument indices
		pattern = [token for token in PAT_REGEX.split(pattern) if token]
		index = 0

		for k in range(len(pattern)):
			if pattern[k] == '{}':
				pattern[k] = index
				index += 1
			elif PAT_REGEX.match(pattern[k]):
				pattern[k] = int(pattern[k][1:-1])

		ops[name] = (pattern, *extra)

	return ops
