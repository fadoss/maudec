#
# Parse MUnit test suites
#

import re

import maude

ASSERT_RE = re.compile(r'^assert(True|False|Equal|LeqSort)\((.+)\)$')


def split_comma(text: str):
	"""Split the text by the first comma"""

	split_point, nesting = None, 0

	for k, c in enumerate(text):
		match c:
			case '(':
				nesting += 1
			case ')':
				nesting -= 1
			case ',':
				if nesting == 0:
					split_point = k

	if split_point is None:
		return None, None

	return text[:split_point], text[split_point + 1:]


def parse_munit(mod: maude.Module, path: str):
	"""Parse MUnit tests"""

	tests = []

	with open(path) as mfile:
		for k, line in enumerate(mfile, start=1):
			if m := ASSERT_RE.fullmatch(line.strip()):
				assert_type = m.group(1)

				if assert_type in ('Equal', 'LeqSort'):
					lhs, rhs = split_comma(m.group(2))

					if lhs is None:
						print(f'{path}:{k}: error: cannot parse assertion, missing second argument')
						continue

					if (lhs := mod.parseTerm(lhs)) is None:
						print(f'{path}:{k}: error: cannot parse assertion left term "{lhs}"')
						continue

					if assert_type == 'Equal':
						if (rhs := mod.parseTerm(rhs, lhs.getSort().kind())) is None:
							print(f'{path}:{k}: error: cannot parse assertion right term "{rhs}"')
							continue

						tests.append(('e', lhs, rhs))

					else:
						if (sort := mod.findSort(rhs.strip())) is None:
							print(f'{path}:{k}: error: cannot parse sort "{rhs.strip()}"')
							continue

						tests.append(('s', lhs, sort))

				else:
					if (term := mod.parseTerm(m.group(2))) is None:
						print(f'{path}:{k}: error: cannot parse assertion term "{m.group(2)}"')
						continue

					tests.append((assert_type[0].lower(), term))

	return tests
