# Contributing to Oflow

Thank you for considering contributing to Oflow! This document provides guidelines for contributing.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How to Contribute

### Reporting Bugs

1. **Check existing issues** to avoid duplicates
2. **Use the bug report template** when creating an issue
3. **Include details**:
   - Your OS and Python version
   - Steps to reproduce
   - Expected vs actual behavior
   - Error messages/logs

### Suggesting Features

1. **Check existing feature requests** first
2. **Open a discussion** before starting work on large features
3. **Explain the use case** and why it's valuable

### Pull Requests

1. **Fork the repository**
2. **Create a feature branch** (`git checkout -b feature/amazing-feature`)
3. **Write tests** for new functionality
4. **Follow code style** (use `ruff format` and `ruff check`)
5. **Update documentation** as needed
6. **Run tests** (`make test`) before submitting
7. **Write clear commit messages**
8. **Submit the PR** with a clear description

## Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/oflow.git
cd oflow

# Create virtual environment
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install -e .[dev]

# Run tests
make test

# Format code
make format

# Lint code
make lint
```

## Code Style

- **Format**: Use `ruff format .`
- **Lint**: Use `ruff check .`
- **Type hints**: Add type hints for function parameters and return values
- **Docstrings**: Add docstrings for public APIs only (avoid unnecessary comments)
- **Line length**: 100 characters max

## Testing

- **Run all tests**: `pytest tests/`
- **Run with coverage**: `pytest --cov=oflow tests/`
- **Test file naming**: `test_*.py`
- **Write meaningful test names**: `test_audio_validation_rejects_empty_audio()`

## Commit Message Guidelines

```
<type>: <subject>

<body>

<footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Adding or updating tests
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `chore`: Maintenance tasks

**Example**:
```
feat: add retry logic with exponential backoff

Implements 3 retry attempts with exponential backoff for
transient API failures. This significantly improves reliability
when network conditions are poor.

Fixes #123
```

## Project Structure

- `oflow` - Main executable script
- `tests/` - Test suite
- `docs/` - Documentation
- `.github/workflows/` - CI/CD configuration

## Questions?

- **Discussions**: https://github.com/CryptoB1/oflow/discussions
- **Issues**: https://github.com/CryptoB1/oflow/issues

Thank you for contributing! 🎉
