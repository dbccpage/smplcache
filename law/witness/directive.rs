pub trait WitnessDirective: crate::law::law_enforcement::sealed::Sealed {
    const GENERATE_LAMBDA_WITNESSES: bool = false;
    const GENERATE_REDUCER_WITNESSES: bool = false;
    const GENERATE_TRANSFORM_WITNESSES: bool = false;

    const REQUIRE_TOTAL: bool = false;
    const REQUIRE_DETERMINISTIC: bool = false;
}

