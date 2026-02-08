#
# Clean up identifiers for the target language
#

# Try to use the regex library, which knows XID_Start and XID_Continue
try:
	import regex as re

	INVALID_CHAR = re.compile(r'^[^\p{XID_Start}a-zA-Z_]|(?<=.)[^\p{XID_Continue}a-zA-Z0-9_]')

# Otherwise, use the builtin re module
except ImportError:
	import re

	INVALID_CHAR = re.compile(r'^[^a-zA-Z_]|(?<=.)[^a-zA-Z0-9_]')


def _badchar_replacement(ch: re.Match):
	"""Replacement of a bad character"""

	return f'u{ord(ch.group(0)):0>4x}'


def _clean_identifier(name: str, replacer=_badchar_replacement):
	"""Clean an identifier of proscribed symbols for most languages"""

	return INVALID_CHAR.sub(replacer, name)


class NameTranslator:
	"""Translate Maude names to be used in the target language"""

	# The translation could be made more precise and translate
	# names for sort, kind, operators, etc. differently and maybe
	# based on the signature. Now, we only translate strings.

	def __init__(self, name_dict: dict[str, str] | None=None):
		self.name_dict = {} if name_dict is None else name_dict

	def translate(self, name: str):
		"""Translate an identifier"""

		if target := self.name_dict.get(name):
			return target

		return _clean_identifier(name)

	__call__ = translate
