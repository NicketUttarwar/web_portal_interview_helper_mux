# Web portal

This repository documents the product. It does not contain the site, Terraform, or the local queue app yet.

The public site is a static HTTPS website on AWS. The queue is stored and run by a separate local Python app. On a period, that app uploads the latest queue into the site on S3, and the HTML CloudFront serves is that publish.

- [What the product is](docs/overview.md)
- [How the repository is laid out](docs/repository.md), including the stacks, structures, and frameworks the code is written in

The local product the runner calls lives in the directory `interview_helper_mux`. These pages name that directory. They describe the portal.

## AWS services

- **Route 53** registers the domain and holds the hosted zone and records that point the domain at CloudFront.
- **ACM** issues the certificate in `us-east-1`, which is where CloudFront reads certificates.
- **S3** holds the private site, including the HTML for the latest queue.
- **CloudFront** serves the site over HTTPS with an origin access control. The queue HTML is served from S3 so a periodic overwrite is what visitors see.

The local Python app is the store and the queue. It writes the latest queue into that HTML on a period. The site does not use DynamoDB, API Gateway, Lambda, or CloudWatch. Logging from the local app is stdout on that machine.

## IAM policies

The IAM user for the access key in `config/secrets/secrets.env` needs these AWS managed policies:

- `AmazonRoute53FullAccess`
- `AmazonS3FullAccess`
- `AWSCertificateManagerFullAccess`
- `CloudFrontFullAccess`

`AWSCertificateManagerFullAccess` includes the service-linked role ACM creates. No other policy is required for this site.
