#
# Entry point of the compiler
#

import re
import sys

import maude

from .compiler import Compiler
from .analyzer import ModuleAnalyzer
from .identifiers import NameTranslator
from . import get_target


def handle_input_file(filename: str):
	"""Handle the input file"""

	spec = None

	if filename.endswith('.json'):
		import json
		with open(filename) as json_file:
			spec = json.load(json_file)

	elif filename.endswith('.yaml'):
		try:
			import yaml
			with open(filename) as yaml_file:
				spec = yaml.safe_load(yaml_file)

		except ImportError:
			print('The yaml package is not installed. Please convert the YAML to '
			      'JSON or install it with pip install pyaml.')
			return None

	elif filename.endswith('.toml'):
		try:
			import tomllib
			with open(filename, 'rb') as toml_file:
				spec = tomllib.load(toml_file)

		except ImportError:
			print('The tomllib package is not available. Please update Python to '
			      'its 3.11 version.')

	elif filename.endswith('.dict'):
		import ast

		with open(filename) as dict_file:
			spec = ast.literal_eval(dict_file.read())

	return spec


def get_module(args):
	"""Select a module from the command-line arguments"""

	# Selected object-level module
	mod = maude.getModule(args.module) if args.module else maude.getCurrentModule()

	if mod is None:
		print(f'Error: cannot find module "{args.module}".')
		return None

	# Select metamodule (if any)
	if args.metamodule:
		# Parse the metamodule term
		module_term = mod.parseTerm(args.metamodule)

		if module_term is None:
			print(f'Error: cannot parse given metamodule in {mod} module.')
			return None

		# Obtain a usable module out of it
		mod = maude.downModule(module_term)

		if mod is None:
			print(f'Error: the given metamodule of sort {module_term.getSort()} is not valid.')
			return None

	return mod


def combine_spec(args, spec):
	"""Combine specification with input arguments"""

	args.source.pop(0)
	args.source.extend(spec.get('source', ()))
	for key, value in spec.items():
		if not args.__dict__.get(key):
			args.__dict__[key] = value


def main():
	import argparse

	parser = argparse.ArgumentParser(description='Experimental Maude compiler')
	parser.add_argument('source', help='Maude source file', nargs='+')
	parser.add_argument('--module', '-m', help='Select the module to compile')
	parser.add_argument('--metamodule', '-M', help='Select a metamodule on the current module')
	parser.add_argument('--analyze', '-a', help='Only analyze the input file', action='store_true')
	parser.add_argument('--filter', help='Filter which functions to compile with a regular expression')
	parser.add_argument('--target', '-t', help='Target language', choices=['cpp', 'rust', 'ast', 'py', 'dafny'], default='cpp')
	parser.add_argument('--tests', help='Path to a MUnit test file')

	args = parser.parse_args()

	# Load the first file as a specification (if it is one)
	if (spec := handle_input_file(args.source[0])) is not None:
		combine_spec(args, spec)

	# Initialize Maude and load source files
	maude.init()

	for source in args.source:
		if not maude.load(source):
			return 1

	# Select the Maude module
	if (module := get_module(args)) is None:
		return 1

	# Analyze the module
	analyzer = ModuleAnalyzer(module)

	if args.analyze:
		import json
		json.dump(analyzer.dump(), sys.stdout)

	else:
		# Name translation
		names = NameTranslator(args.name_map) if 'name_map' in args else None

		# Language-specific generator
		generator = get_target(args.target)

		# Compile to the target language
		compiler = Compiler(module, analyzer, generator=generator, name_translator=names)

		# Parse test expressions (if any)
		tests = ()

		if args.tests:
			from .munit import parse_munit
			tests = parse_munit(module, args.tests)

		filter = re.compile(args.filter) if args.filter else None
		compiler.compile(filter=filter, tests=tests)

	return 0


sys.exit(main())
