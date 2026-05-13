use serde::{Deserialize, Serialize};

pub trait ValidationViolation: core::fmt::Debug + Send + Sync + Serialize + 'static {
    fn code(&self) -> &'static str;
}
