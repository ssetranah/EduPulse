# web

`public/` is a static, dependency-free dashboard scaffold: a persistent SYNTHETIC_TEST_DATA banner and
an API health card. Served by nginx in compose, which proxies `/api/` to the API container.
Real dashboard views arrive after the API exposes data.
