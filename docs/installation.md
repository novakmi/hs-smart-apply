# Installation

## From a local checkout

Clone the repository and install it with pip:

```sh
git clone https://github.com/novakmi/hs-smart-apply.git
cd hs-smart-apply
python -m pip install .
```

For development, install in editable mode so changes in the checkout are used
immediately:

```sh
python -m pip install -e .
```

## Directly from GitHub

Install the current repository version without cloning it first:

```sh
python -m pip install git+https://github.com/novakmi/hs-smart-apply.git
```

Each method installs the `hs-smart-apply` command into the active Python
environment. Apply mode also requires `herdr-spreader` to be installed and
available on PATH.

After installation, run the executable with a layout file:

```sh
hs-smart-apply layout.yaml
```

To run the project directly from source without installing it, from the project
root use:

```sh
python -m pip install PyYAML
PYTHONPATH=src python -m hs_smart_apply layout.yaml
```
