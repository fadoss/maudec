## Experimental compiler for Maude

This is an experimental compiler from a subset of Maude to some imperative programming languages like C++, Rust, Python, and Dafny. It tries to exploit their support for algebraic data types and pattern matching when available. Non-deterministic rewriting, AC operators, and complex associative patterns are not supported yet.

The program can be executed as
```
$ python -m maudec <input file> [-t <language>]
```
where the input file is a Maude file (or many of them) or a JSON, TOML or YAML file specifying a renaming in addition to a list of Maude files (with [this schema](maudec/data/maudec.schema.json)). The language can be `cpp`, `rust`, `py`, `dafny`, or `ast`.

When `ast` is used, a graph with the abstract syntax tree for the Maude operators in the internal intermediate representation is written in [Graphviz](https://graphviz.org/)'s DOT format. The following command produces a PDF with the graphical representation of the AST:
```
$ python -m maudec <input file> -t ast --filter <operator name> | dot -Tpdf > output.pdf
```

The `tests` directory include some examples and unit tests on them for the [MUnit](https://github.com/ariesco/MUnit) tool. `maudec` can read these with the `--tests` argument and produce tests in the corresponding target languages. Moreover, the `tester.py` script can be used to test the compilation and behavior of the translations. Compilers or interpreters for the target languages are required to do so. 

Python 3.10 is needed to run this program (or Python 3.11 for reading TOML files) and it depends on the [`maude`](https://pypi.org/project/maude/) package. If the [`regex`](https://pypi.org/project/regex/) module is available, it will use it to improve the escaping of identifiers.
