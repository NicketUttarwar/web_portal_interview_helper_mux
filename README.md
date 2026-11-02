# Web portal

This repository documents the product. It does not contain the site, the API, Terraform, or the local executable yet.

The product is a public HTTPS website on AWS and a key-authenticated executable on the operator's computer. Visitors submit inputs. The queue is visible on the site. The executable claims each input, runs an allowlisted command in a fresh local folder, and publishes status and files back to the site.

- [What the product is](docs/overview.md)
- [How the repository is laid out](docs/repository.md), including the stacks, structures, and frameworks the code is written in

The local product the runner calls lives in the directory `interview_helper_mux`. These pages name that directory. They describe the portal.
