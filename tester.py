#
# Benchmark and test translations
#

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from multiprocessing import Process, Queue
from traceback import print_exception, format_exception
import subprocess
import os
import shutil


class CppCompiler:
	"""Compiler connector for C++"""

	CXX = os.environ.get('CXX', 'c++')
	FNAME = 'output.cc'
	LANG = 'cpp'

	@staticmethod
	def available():
		return shutil.which(self.CXX) is not None

	def build(self, source, root, quiet, tests=False):
		output = subprocess.PIPE if quiet else None
		build_type = '-oa.out' if tests else '-c'
		ret = subprocess.run((self.CXX, '-std=c++20', build_type, source), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0

	def run(self, root, quiet):
		output = subprocess.PIPE if quiet else None
		ret = subprocess.run((f'./a.out',), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0


class RustCompiler:
	"""Compiler connector for Rust"""

	FNAME = 'output.rs'
	LANG = 'rust'

	@staticmethod
	def available():
		return shutil.which('rustc') is not None

	def build(self, source, root, quiet, tests=False):
		output = subprocess.PIPE if quiet else None
		build_type = '--test' if tests else '--crate-type=lib'
		ret = subprocess.run(('rustc', build_type, source), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0

	def run(self, root, quiet):
		output = subprocess.PIPE if quiet else None
		ret = subprocess.run((f'./output',), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0


class PythonCompiler:
	"""Compiler connector for Python"""

	FNAME = 'output.py'
	LANG = 'py'

	@staticmethod
	def available():
		return shutil.which('python') is not None

	def build(self, source, root, quiet, tests=False):
		output = subprocess.PIPE if quiet else None
		ret = subprocess.run(('python', '-c', 'import output'), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0

	def run(self, root, quiet):
		output = subprocess.PIPE if quiet else None
		ret = subprocess.run(('python', self.FNAME), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0


class DafnyCompiler:
	"""Compiler connector for Dafny"""

	FNAME = 'output.dfy'
	LANG = 'dafny'

	@staticmethod
	def available():
		return shutil.which('dafny') is not None

	def build(self, source, root, quiet, tests=False):
		output = subprocess.PIPE if quiet else None
		ret = subprocess.run(('dafny', 'resolve', source), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0

	def run(self, root, quiet):
		output = subprocess.PIPE if quiet else None
		ret = subprocess.run(('dafny', 'verify', self.FNAME), stdout=output, stderr=output, cwd=root)
		return ret.returncode == 0


def _run_test(test, tmpdir, queue, compiler, quiet, with_tests):
	"""Run a test in a separte process"""

	try:
		import maude
		import maudec

		maude.init()
		maude.load(str(test))
		m = maude.getCurrentModule()

		# Check whether there are tests
		tests_file = test.with_stem(f'{test.stem}-test')

		if with_tests and tests_file.exists():
			import maudec.munit
			tests = maudec.munit.parse_munit(m, tests_file)
		else:
			tests = ()

		with (tmpdir / compiler.FNAME).open('w') as out:
			maudec.compile(m, out=out, generator=compiler.LANG, tests=tests)

		# Compilation
		result = compiler.build(compiler, compiler.FNAME, tmpdir, quiet, tests=bool(tests))

		if not result:
			queue.put((result, 'compilation'))
			return

		if not tests:
			queue.put((True, None))
			return

		# Test running
		result = compiler.run(compiler, tmpdir, quiet)

		queue.put((result, 'testing'))

	except Exception as e:
		# print_exception(e)
		msg = format_exception(e, limit=1)[-1].rstrip()
		queue.put((msg, 'generation'))


def get_compiler(lang: str):
	"""Get compiler handler for a language"""

	match lang:
		case 'rust':
			return RustCompiler

		case 'py':
			return PythonCompiler

		case 'dafny':
			return DafnyCompiler

		case _:
			return CppCompiler


def run_tests(tests, compilers, quiet=False, with_tests=False):
	"""Run tests"""

	failed = []

	for test in tests:
		print(f'Testing {test}...')
		with TemporaryDirectory() as tmpdir:
			for compiler in compilers:
				queue = Queue()
				p = Process(target=_run_test, args=(test, Path(tmpdir), queue, compiler, quiet, with_tests))
				p.start()

				# Wait for a message
				msg, reason = queue.get()

				if msg is True:
					print('  ✅')
				else:
					print('  ❌', msg)
					failed.append((test, reason))

				p.join()

	print(f'\nSummary: {len(failed)}/{len(tests)} failed test cases')
	for test, reason in failed:
		print(f'  {test} ({reason})')


def main():
	import argparse

	parser = argparse.ArgumentParser(description='Test the Maude to imperative language translation')
	parser.add_argument('test', help='Tests to test', nargs='*', type=Path)
	parser.add_argument('--target', '-t', help='Target language', choices=('cpp', 'rust', 'py', 'dafny'))
	parser.add_argument('--quiet', '-q', help='Do not show compiler output', action='store_true')
	parser.add_argument('--no-test', help='Do not run tests', action='store_true')

	args = parser.parse_args()

	tests = args.test

	if not tests:
		tests = [p for p in Path('tests').glob('*.maude') if not p.name.endswith('-test.maude')]

	# Try with all compiler if none is given
	compilers = (get_compiler(args.target),) if args.target else (CppCompiler, RustCompiler, PythonCompiler, DafnyCompiler)

	# Remove those compilers that are not available
	avail_compilers = []

	for compiler in compilers:
		if not compiler.available():
			print(f'⚠️ Warning: removing compiler for {compiler.LANG} because it is not available.')
		else:
			avail_compilers.append(compiler)

	run_tests(tests, avail_compilers, args.quiet, not args.no_test)


if __name__ == '__main__':
	sys.exit(main())
