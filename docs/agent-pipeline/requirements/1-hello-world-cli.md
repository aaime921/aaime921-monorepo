# Requirements: Hello World CLI Greeting

Issue: #1

## Summary

The BO wants a minimal command-line tool that prints a greeting of the form
`Hello, <name>!`, where `<name>` comes from a command-line argument. If no
name is supplied, the tool defaults to greeting "world". This is explicitly
a smoke test of the agent pipeline itself (not a real product feature), so
the scope is intentionally kept trivial.

## Scope

- A CLI tool, invoked from a terminal, that accepts an optional single
  positional argument representing a name.
- When a name argument is provided, the tool prints `Hello, <name>!` to
  standard output.
- When no name argument is provided, the tool prints `Hello, world!` to
  standard output.
- At least one automated test covering the tool's behavior.
- Implementation language/framework choice is left to the Architect/Developer
  (BO stated "any language is fine; pick something simple to test in this
  environment").

### Out of scope

- Input validation, sanitization, or error handling beyond the basic
  default-to-"world" behavior (e.g. no requirement to handle multiple
  arguments, flags, or special characters specially).
- Internationalization/localization of the greeting.
- Packaging, distribution, or installation tooling (e.g. publishing to a
  package registry).
- Any interactive/prompt-based input — the name is a command-line argument,
  not something the user is asked for interactively.

## Acceptance criteria

1. Running the CLI with a name argument (e.g. `<tool> Alice`) prints exactly
   `Hello, Alice!` to standard output.
2. Running the CLI with no arguments prints exactly `Hello, world!` to
   standard output.
3. The tool exits with a success (zero) exit code in both of the above cases.
4. At least one automated test exists that verifies the with-argument
   behavior (criterion 1), and at least one automated test exists that
   verifies the no-argument default behavior (criterion 2).
5. The automated test(s) can be run via a single documented command and pass
   in this environment.

## Open questions

None blocking. The BO's request is small and unambiguous enough to proceed
without further clarification.
