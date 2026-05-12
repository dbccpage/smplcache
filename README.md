# smplcache
SmplCache is a small proof-of-concept for query-shape dependency fingerprinting. It avoids false cache invalidation by intersecting CDC-style write deltas with cached query dependencies, and it demonstrates when aggregates can be incrementally repaired instead of dropped.
