# Publishing

# Install build tools
~~~bash
pip install build twine
~~~

# Build the package
~~~bash
python3 -m build
~~~

# Check the build
~~~bash
twine check dist/*
~~~

# Upload to TestPyPI (dry run)
~~~bash
twine upload --repository testpypi dist/*
~~~

# Upload to PyPI (for real)
~~~bash
twine upload dist/*
~~~
