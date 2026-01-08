# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-01-05

### Added
- LangChain architecture rebuild for 100% reliability
- Audio validation (empty/silent detection)
- Retry logic with exponential backoff (3 attempts)
- Event-driven error handling for clear visibility
- Comprehensive test suite (8/8 passing)
- Gemini 2.5 Flash support via OpenRouter (experimental)
- Professional documentation (CONTRIBUTING.md, CODE_OF_CONDUCT.md)
- CI/CD pipeline configuration

### Changed
- Switched from blocking threads to async/await architecture
- Improved error messages with specific failure reasons
- Better project structure with tests/ and docs/ directories
- Updated README with comprehensive documentation

### Fixed
- Race conditions in audio capture
- Missing validation causing failed API calls with empty audio
- No retry on transient network failures
- Generic error messages with no actionable information

## [0.1.0] - 2026-01-04

### Added
- Initial release
- OpenAI gpt-4o-audio-preview support
- Basic Whisper local transcription
- Hyprland/Wayland integration
- Global hotkey support (Super+I)
- Auto-paste functionality
- Smart text formatting
- MIT License

[0.2.0]: https://github.com/CryptoB1/omarchyflow/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CryptoB1/omarchyflow/releases/tag/v0.1.0
