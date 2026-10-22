# Terraform: GCP demo environment

Creates a VPC with private GKE nodes and Cloud NAT, a zonal GKE cluster set up for Agent Substrate
with one Spot node pool, and a monthly budget alert. Cloud SQL, buckets, Artifact Registry, secrets
and Workload Identity come in the next PR.

Region: `europe-west1` (Belgium), an EU region in the lowest GCP price tier with GKE, Cloud SQL
Postgres 18 and T2D machines. `europe-central2` (Warsaw) charges more for VMs and Cloud SQL.

## One-time bootstrap (by hand)

You need Owner on the project, and Billing Account Costs Manager on its billing account (for the budget).

```sh
export PROJECT_ID=<your-project-id>
gcloud auth login && gcloud auth application-default login
gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com --project "$PROJECT_ID"

# Remote state bucket. Terraform never manages it, so destroy can't delete it.
gcloud storage buckets create "gs://$PROJECT_ID-tfstate" --project "$PROJECT_ID" \
  --location europe-west1 --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$PROJECT_ID-tfstate" --versioning

cp terraform.tfvars.example terraform.tfvars   # git-ignored; fill in project and billing account
```

## Apply

```sh
terraform init -backend-config="bucket=$PROJECT_ID-tfstate"
terraform plan -out tfplan
terraform apply tfplan
```

One apply creates everything in dependency order (about 15 minutes). `terraform output -raw cluster`
prints the command that fetches cluster credentials. To pause, put `node_count = 0` in `terraform.tfvars` and apply. A `-var` flag would be undone by the
next plain apply. If Spot capacity runs out in the zone, set `spot_nodes = false`.

## Monthly cost estimate

Rough `europe-west1` list prices in USD, with the defaults. Check the current pricing pages.

| Item | Cost |
|---|---|
| GKE control plane (one zonal cluster, covered by the GKE free tier) | $0 |
| 1 × `t2d-standard-4` Spot node (on-demand would be ~$135) | ~$31 |
| 50 GB balanced node disk | ~$6 |
| Cloud NAT gateway and its external IP, plus traffic | ~$5 |
| Logging, Monitoring, Trace (mostly inside free allotments; heavy tracing costs more) | $0–5 |
| **Total** | **~$42–47** |

The free tier covers one zonal cluster per billing account (a second one pays ~$73). Not included:
a load balancer for the api (~$18) and internet egress. With `node_count = 0` it drops to about $5.

## Teardown

First delete what Kubernetes created (load balancers, disks), or it blocks the VPC delete and keeps billing:

```sh
kubectl delete namespace justpass ate-system     # waits until their load balancers and disks are gone
# set deletion_protection = false in terraform.tfvars, then:
terraform apply                                  # lifts protection
terraform destroy
gcloud storage rm -r "gs://$PROJECT_ID-tfstate"  # optional: the state bucket
```

## How this matches Substrate's `setup-gcp`

Same as `setup-gcp create cluster`: the two `certificates.k8s.io/v1beta1` APIs enabled at cluster
creation, Workload Identity, Dataplane V2, Managed OpenTelemetry, Filestore CSI off, and a
`substrate-node-pool` with the high-density sysctls. Nodes get `roles/artifactregistry.reader`, as in
`setup-gcp create iam`.

Deliberate differences, all for cost or safety:

- `t2d-standard-4` Spot, one node with a 50 GB disk, no nested virtualization, instead of two
  `c3-standard-4` with 100 GB. We run gVisor workers only; micro-VM workers would need C3 and nested
  virtualization. T2D runs on a single CPU platform, so a snapshot taken on one node restores on any
  other.
- A dedicated `justpass` VPC instead of `default`. Nodes use their own service account and have no
  public IPs. The control plane keeps a public endpoint, guarded by Google IAM.
