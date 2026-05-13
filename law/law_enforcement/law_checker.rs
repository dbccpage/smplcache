pub trait LawCheck<S> {
    type Witness;
    type Violation;
    fn name(&self) -> &'static str;
    fn check(&self, subject: &S) -> Result<Self::Witness, Self::Violation>;
}

pub trait ErasedLawCheck<S> {
    fn name(&self) -> &'static str;
    fn check_erased(&self, subject: &S) -> Result<Box<dyn std::any::Any>, Box<dyn std::any::Any>>;
}

impl<S, T: LawCheck<S>> ErasedLawCheck<S> for T
where
    T::Witness: 'static,
    T::Violation: 'static,
{
    fn name(&self) -> &'static str {
        LawCheck::name(self)
    }

    fn check_erased(&self, subject: &S) -> Result<Box<dyn std::any::Any>, Box<dyn std::any::Any>> {
        LawCheck::check(self, subject)
            .map(|w| Box::new(w) as Box<dyn std::any::Any>)
            .map_err(|v| Box::new(v) as Box<dyn std::any::Any>)
    }
}

pub struct LawCheckReport {
    pub law: &'static str,
    pub passed: bool,
    pub evidence: Result<Box<dyn std::any::Any>, Box<dyn std::any::Any>>,
}

/// Check all laws against a subject and collect reports.
pub fn check_all_laws<S>(subject: &S, laws: &[&dyn ErasedLawCheck<S>]) -> Vec<LawCheckReport> {
    laws.iter()
        .map(|law| {
            let result = law.check_erased(subject);
            LawCheckReport {
                law: law.name(),
                passed: result.is_ok(),
                evidence: result,
            }
        })
        .collect()
}

/// Returns true only if every law in the batch passes.
pub fn all_laws_hold<S>(subject: &S, laws: &[&dyn ErasedLawCheck<S>]) -> bool {
    laws.iter().all(|law| law.check_erased(subject).is_ok())
}

/// Returns the names of all failing laws.
pub fn failing_laws<S>(subject: &S, laws: &[&dyn ErasedLawCheck<S>]) -> Vec<&'static str> {
    laws.iter()
        .filter(|law| law.check_erased(subject).is_err())
        .map(|law| law.name())
        .collect()
}

// â”€â”€â”€ Tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#[cfg(test)]
mod tests {
    use super::*;

    // Subject: a simple pair of i64s
    struct Pair(i64, i64);

    // Law: sum is positive
    struct SumPositive;
    impl LawCheck<Pair> for SumPositive {
        type Witness = ();
        type Violation = ();
        fn name(&self) -> &'static str {
            "sum_positive"
        }
        fn check(&self, s: &Pair) -> Result<(), ()> {
            if s.0 + s.1 > 0 { Ok(()) } else { Err(()) }
        }
    }

    // Law: product is non-negative
    struct ProductNonNeg;
    impl LawCheck<Pair> for ProductNonNeg {
        type Witness = ();
        type Violation = ();
        fn name(&self) -> &'static str {
            "product_non_negative"
        }
        fn check(&self, s: &Pair) -> Result<(), ()> {
            if s.0 * s.1 >= 0 { Ok(()) } else { Err(()) }
        }
    }

    // Law: always fails
    struct AlwaysFails;
    impl LawCheck<Pair> for AlwaysFails {
        type Witness = ();
        type Violation = ();
        fn name(&self) -> &'static str {
            "always_fails"
        }
        fn check(&self, _s: &Pair) -> Result<(), ()> {
            Err(())
        }
    }

    #[test]
    fn check_all_laws_all_pass() {
        let subject = Pair(3, 5);
        let laws: Vec<&dyn ErasedLawCheck<Pair>> = vec![&SumPositive, &ProductNonNeg];
        let reports = check_all_laws(&subject, &laws);
        assert_eq!(reports.len(), 2);
        assert!(reports.iter().all(|r| r.passed));
    }

    #[test]
    fn check_all_laws_mixed_results() {
        let subject = Pair(3, -5);
        let laws: Vec<&dyn ErasedLawCheck<Pair>> = vec![&SumPositive, &ProductNonNeg];
        let reports = check_all_laws(&subject, &laws);
        assert!(!reports[0].passed);
        assert!(!reports[1].passed);
    }

    #[test]
    fn all_laws_hold_returns_false_when_any_fails() {
        let subject = Pair(3, 5);
        let laws: Vec<&dyn ErasedLawCheck<Pair>> = vec![&SumPositive, &AlwaysFails];
        assert!(!all_laws_hold(&subject, &laws));
    }

    #[test]
    fn all_laws_hold_returns_true_when_all_pass() {
        let subject = Pair(3, 5);
        let laws: Vec<&dyn ErasedLawCheck<Pair>> = vec![&SumPositive, &ProductNonNeg];
        assert!(all_laws_hold(&subject, &laws));
    }

    #[test]
    fn failing_laws_returns_only_failures() {
        let subject = Pair(3, 5);
        let laws: Vec<&dyn ErasedLawCheck<Pair>> = vec![&SumPositive, &AlwaysFails, &ProductNonNeg];
        let failures = failing_laws(&subject, &laws);
        assert_eq!(failures, vec!["always_fails"]);
    }

    #[test]
    fn failing_laws_returns_empty_when_all_pass() {
        let subject = Pair(3, 5);
        let laws: Vec<&dyn ErasedLawCheck<Pair>> = vec![&SumPositive, &ProductNonNeg];
        let failures = failing_laws(&subject, &laws);
        assert!(failures.is_empty());
    }
}
