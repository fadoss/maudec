# Improvement of generated code by AI models

The structure of this directory is as follows:

* `improve.py` is used to obtain optimized code from a supported language model (namely, `gemini-*`, `gemma-*`, and `devstral-small-2`, although it can be easily extended for others). The `GEMINI_API_KEY` environment variable should be defined to use the Google AI API.
* `check.py` is used to check whether a translated program compiles (`compile` subcommand), test whether it is equivalent (`diff`), and benchmark it (`bench`).
* `make_test.py` is used to generate test inputs for the given file with the supported models. `GEMINI_API_KEY` should be defined as well.
* `missing.py` and `missing_inputs.py` are used to generate the missing translations of the code in `tests/original`, or the missing inputs in `inputs/<model>`.

More information can be found in [*Improving generated programs using large language models*](https://hdl.handle.net/11705/PROLE/2026/14) (PROLE 2026).
