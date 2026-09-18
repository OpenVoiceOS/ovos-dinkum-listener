# Suggestions for ovos-dinkum-listener

- **Consolidate Test Helpers:** The test setup logic in `test_service.py` and `test_service_extended.py` is very similar. Consolidating the service creation and mocking into a shared helper function would reduce code duplication and make the tests easier to maintain.
- **Improve Docstrings:** Many docstrings are too long and could be more concise.
- **Pin GitHub Actions:** Pin all GitHub Actions to a specific commit SHA to improve security.
