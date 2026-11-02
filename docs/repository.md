# Repository scaffold

This file is the contract for how the repository is laid out and how the code will be built. Config templates, gitignore, `ASSETS/.gitkeep`, and `terraform/state/` exist. Application code, Terraform resources, and scripts do not.

## Intended tree

- `config/app.defaults.json` — non-secret defaults
- `config/secrets/secrets.env` — credentials, gitignored
- `config/templates/secrets.env.example` — blank template, committed
- `config/terraform.tfvars.example` — committed
- `config/terraform.tfvars` — gitignored
- `ASSETS/.gitkeep` — committed; job folders and other local files land under `ASSETS/` and stay untracked
- `terraform/` — infrastructure, with live state at `terraform/state/terraform.tfstate`
- `scripts/bootstrap_venv.sh` — create the virtualenv
- `scripts/run.sh` — start the app
- `scripts/tf-*.sh` — the only way to run Terraform
- `site/` — static page
- `lambda/` — HTTP API
- `agent/` — local executable and command allowlist
- `docs/overview.md` — what the product is
- `docs/repository.md` — this file

What each code home is for is described in [overview.md](overview.md).

## Defaults

Every non-secret default goes in `config/app.defaults.json`. That file is for fixed, important values: directory names, file locations, and other hardcoded defaults this app needs. Secrets do not go in that file.

The first keys, when that file is added:

- `assets_dir` — `ASSETS`, the root for local files that stay out of git.
- `job_dir_pattern` — `ASSETS/jobs/{job_id}`, one fresh folder per claimed job.
- `product_dir` — `interview_helper_mux`, the checkout the allowlisted command runs from.
- `secrets_file` — `config/secrets/secrets.env`.
- `terraform_var_file` — `config/terraform.tfvars`.

## Secrets

Credentials go only in `config/secrets/secrets.env`. That includes the agent bearer key and the AWS credentials. Gitignore the whole `config/secrets/` directory. Commit a blank template at `config/templates/secrets.env.example`.

Code reads `config/secrets/secrets.env` for itself. It does not export that file onto the whole process environment. A job process does not receive the agent key or the AWS credentials.

Terraform wrappers are the one loader that reads the file in order to pass credentials into Terraform. They do not dump those values into the shell for every later command.

## Local files

`ASSETS/` at the repo root is the place for local files that should not be in git, even when little is stored there at first. Per-job work folders live here, under the `job_dir_pattern` in `config/app.defaults.json`. Gitignore `/ASSETS/`. Commit `/ASSETS/.gitkeep`.

## Terraform

Infrastructure is Terraform on AWS: a private S3 origin, CloudFront, ACM, the HTTP API, and the queue table. The region is `us-east-1`, because CloudFront certificates are issued there. The app does not require the AWS CLI or `aws login`.

When Terraform exists:

- Commit the live state at `terraform/state/terraform.tfstate`.
- Gitignore other `*.tfstate` files, then un-ignore only that committed state file.
- Gitignore `config/terraform.tfvars`.
- Commit `config/terraform.tfvars.example`.
- Run Terraform only through wrapper scripts. Those scripts load `config/secrets/secrets.env` and pass `config/terraform.tfvars`.

## Python

This repo has its own `.venv` on Python 3.12, plus a lockfile. Gitignore `.venv`. Setup is `scripts/bootstrap_venv.sh`. Starting the app is `scripts/run.sh`. One of each.

## How the code is written

Portal code uses the stacks, structures, and frameworks below. `product_dir` is the directory `interview_helper_mux`. These pages name that directory and describe the portal.

### Stacks

- Python 3.12 for the API and the local executable. Direct dependencies are pinned, and the lockfile is what bootstrap installs.
- The site is React, TypeScript, and Vite. Client payloads are checked with Zod. Styles live in one CSS file.
- Infrastructure is Terraform on AWS. Runtime calls to S3 and DynamoDB use boto3 with credentials from `config/secrets/secrets.env`.
- Tests are pytest for Python and Vitest for the site.

### Structures

- Python lives in one package under `src/`. Modules split by job: submit and claim, the command allowlist, the job folder, and the publish back to the site. Request bodies, status rows, and files written as JSON are pydantic models.
- The site lives in `site/`. Screens are function components under `site/src/components/`. Shared queue state sits in one context. The Vite build is what CloudFront serves.
- Terraform is one file per resource family: S3, CloudFront, ACM, the HTTP API, Lambda, DynamoDB, and IAM. Wrappers in `scripts/tf-*.sh` are the only entry.
- Tests live in `tests/`, next to the behavior they lock.

### Frameworks

| Layer | Framework |
|---|---|
| HTTP handlers | Python functions with pydantic models, behind API Gateway |
| Local executable | A Typer command that owns the poll loop and the allowlist |
| Site | React function components, TypeScript, Vite, Zod |
| AWS infra | Terraform |
| AWS from code | boto3 |
| Python tests | pytest |
| Site tests | Vitest |

## Gitignore

Anchor these at the repo root:

- `/config/secrets/`
- `/ASSETS/`
- `.venv`
- `config/terraform.tfvars`
- `*.tfstate`, with an exception that un-ignores only `terraform/state/terraform.tfstate`
