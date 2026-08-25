# Testing Requirements

## Minimum Test Coverage: 80%

Test Types (ALL required):
1. **Unit Tests** - Individual functions, utilities, components
2. **Integration Tests** - API endpoints, database operations
3. **E2E Tests** - Critical user flows (framework chosen per language)

## Test File Placement

Tests live in a test folder **inside the folder that owns the code under test**. Neither subproject
has a separate top-level test tree.

- **Python** (`twinarm/`) — a `tests/` package, with `__init__.py`, beside the code it covers:
  `src/twinarm/api/features/health/tests/test_health.py` covers the `health` slice.
- **TypeScript / React** (`twinarm-web-ui/`) — a `__tests__/` folder inside the segment:
  `src/features/ff-mode/ui/__tests__/FfModeSwitch.test.tsx` covers `ui/FfModeSwitch.tsx`.

Adding a test folder needs no config change: pytest collects from `src`, and the Vitest include
glob already reaches into `__tests__/`. Both tool defaults keep test files out of coverage.

Playwright end-to-end specs are the one exception — they cover user flows rather than a folder, and
stay in `twinarm-web-ui/e2e/`.

## Test-Driven Development

MANDATORY workflow:
1. Write test first (RED)
2. Run test - it should FAIL
3. Write minimal implementation (GREEN)
4. Run test - it should PASS
5. Refactor (IMPROVE)
6. Verify coverage (80%+)

## Troubleshooting Test Failures

1. Use **tdd-guide** agent
2. Check test isolation
3. Verify mocks are correct
4. Fix implementation, not tests (unless tests are wrong)

## Agent Support

- **tdd-guide** - Use PROACTIVELY for new features, enforces write-tests-first

## Test Structure (AAA Pattern)

Prefer Arrange-Act-Assert structure for tests:

```typescript
test('calculates similarity correctly', () => {
  // Arrange
  const vector1 = [1, 0, 0]
  const vector2 = [0, 1, 0]

  // Act
  const similarity = calculateCosineSimilarity(vector1, vector2)

  // Assert
  expect(similarity).toBe(0)
})
```

### Test Naming

Use descriptive names that explain the behavior under test:

```typescript
test('returns empty array when no markets match query', () => {})
test('throws error when API key is missing', () => {})
test('falls back to substring search when Redis is unavailable', () => {})
```
