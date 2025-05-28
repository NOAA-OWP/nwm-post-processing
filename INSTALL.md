# Installation instructions

To install the python code and get it ready for development, just enter the following into your shell from the root of the project:

```shell
# Assuming `python3` is linked to a version of Python 3.10 and up
#   Substitute commands like `python3.10`, `python3.11`, `python3.12`, etc as appropriate
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip
pip install -e .
```

This will install everything into your environment along with all of the requirements.
