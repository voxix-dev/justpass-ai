# Web app

Owned by the frontend teammate; the stack is their choice.

The only interface to the backend is `contracts/openapi.json`. Generate a typed client from it
instead of writing API calls by hand. CI regenerates the contract and fails if it changed unexpectedly.
