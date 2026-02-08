import sys

__version__ = 'dev'


def get_target(target, out=sys.stdout):
	"""Select target language"""

	match target:
		case 'cpp':
			from .generators.cpp import CppGenerator
			return CppGenerator(out=out)

		case 'rust':
			from .generators.rust import RustGenerator
			return RustGenerator(out=out)

		case 'ast':
			from .generators.dot import DotGenerator
			return DotGenerator(out=out)

		case 'py':
			from .generators.python import PythonGenerator
			return PythonGenerator(out=out)

		case 'dafny':
			from .generators.dafny import DafnyGenerator
			return DafnyGenerator(out=out)


def compile(mod: 'maude.Module', filter=None, out=None, name_map=None, generator=None, tests=()):
	"""Compile a Maude module to the target language"""

	from .analyzer import ModuleAnalyzer
	from .compiler import Compiler
	from .identifiers import NameTranslator
	import sys

	out = out if out else sys.stdout

	if isinstance(generator, str):
		generator = get_target(generator, out=out)

	names = NameTranslator(name_map) if name_map is not None else None
	analyzer = ModuleAnalyzer(mod)
	compiler = Compiler(mod, analyzer, out=out, generator=generator, name_translator=names)
	compiler.compile(filter=filter, tests=tests)
