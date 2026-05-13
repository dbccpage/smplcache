# Global Conventions

Before the discipline lists, here are cross-cutting “contract patterns” you’ll want throughout:

## Contract pattern A — algebraic laws as *property tests*

For any law like associativity, identity, functor laws, $$d^2=0$$, etc.:

* Provide a `Law` module with **proptests** (or deterministic finite checks when possible).
* Provide “witness” constructors for small counterexamples in debug builds.
* Keep the law *documented on the trait* and *testable via helpers*.

## Contract pattern B — invariants as *types*

Prefer:

* `struct NonZero<T>(T);` over `T` with a runtime check
* `struct FiniteDim<const N: usize>` for dimension
* `struct Verified<T, P>` where `P` is a proof marker you can only obtain via checked constructors

## Contract pattern C — total vs partial APIs

For operations that may fail:

* Provide `try_*` returning `Result<_, Error>`
* Provide `*_unchecked` behind `unsafe` or internal modules
* Make domain constraints explicit (e.g., invertibility)
