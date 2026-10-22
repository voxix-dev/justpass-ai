# Terraform: GCP demo environment

Creates everything the demo runs on, in one project and one EU region:

- VPC with private GKE nodes, Cloud NAT for outbound traffic, private services access.
- Zonal GKE cluster set up for Agent Substrate, with one Spot node pool.
- Cloud SQL Postgres 18 (private IP, IAM login only) with the `app` and `atepg` databases.
- Buckets: `<project>-app-data`, `<project>-snapshots` (cleaned after 3 days), `<project>-snapshots-assets`.
- Artifact Registry Docker repo, service accounts and Workload Identity bindings, Secret Manager secrets
  (names only), and a monthly budget alert.

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

One apply creates everything in dependency order (about 15 minutes). Then, once:

1. Cluster credentials: `terraform output -raw cluster` prints the `gcloud` command.
2. Secret values: `printf %s "$KEY" | gcloud secrets versions add OPENROUTER_API_KEY --data-file=-`
   (same for `SEARCH_API_KEY`). Secret Manager is the source of truth; copy the values into the
   Kubernetes Secret that Substrate's egress gateway reads. No workload reads Secret Manager.
3. Substrate: create the roles and grants in `atepg` as in Substrate's `tools/setup-gcp/cloud-sql.md`,
   section 2, then install it with `ATE_API_POSTGRES_CLOUDSQL_INSTANCE` (`terraform output -raw
   cloudsql_instance`), `ATE_API_POSTGRES_CLOUDSQL_GSA` (`terraform output service_accounts`),
   `BUCKET_NAME` and `SNAPSHOT_LOCATION` (`snapshot_bucket`, `snapshot_location`). Actors go in the
   `justpass` atespace: the 3-day snapshot cleanup only covers that atespace.
4. App database: with the same temporary `postgres` password, connect to `app` and run
   `GRANT ALL ON SCHEMA public TO "justpass-api@<project>.iam";`. Then scramble the password
   (`gcloud sql users set-password postgres --instance=justpass --password="$(openssl rand -hex 16)"`):
   every pod in the cluster, agents included, can reach the database's private IP.

To pause, put `node_count = 0` in `terraform.tfvars` and apply. A `-var` flag would be undone by the
next plain apply. If Spot capacity runs out in the zone, set `spot_nodes = false`.

## Monthly cost estimate

Rough `europe-west1` list prices in USD, with the defaults. Check the current pricing pages.

| Item | Cost |
|---|---|
| GKE control plane (one zonal cluster, covered by the GKE free tier) | $0 |
| 1 × `t2d-standard-4` Spot node (on-demand would be ~$135) | ~$31 |
| 50 GB balanced node disk | ~$6 |
| Cloud NAT gateway and its external IP, plus traffic | ~$5 |
| Cloud SQL `db-g1-small`, 10 GB SSD, backups | ~$28 |
| Buckets, Artifact Registry, Secret Manager | < $1 |
| Logging, Monitoring, Trace (mostly inside free allotments; heavy tracing costs more) | $0–5 |
| **Total** | **~$70–75** |

The free tier covers one zonal cluster per billing account (a second one pays ~$73). Not included:
a load balancer for the api (~$18) and internet egress. With `node_count = 0` it drops to about $33.

## Teardown

First delete what Kubernetes created (load balancers, disks), or it blocks the VPC delete and keeps billing:

```sh
kubectl delete namespace justpass ate-system     # waits until their load balancers and disks are gone
# set deletion_protection = false in terraform.tfvars, then:
terraform apply                                  # lifts protection; buckets may now be emptied
terraform destroy                                # deletes every bucket's contents too
gcloud storage rm -r "gs://$PROJECT_ID-tfstate"  # optional: the state bucket
```

A deleted Cloud SQL instance name can't be reused for about a week.

## How this matches Substrate's `setup-gcp`

Same as `setup-gcp create cluster`: the two `certificates.k8s.io/v1beta1` APIs enabled at cluster
creation, Workload Identity, Dataplane V2, Managed OpenTelemetry, Filestore CSI off, and a
`substrate-node-pool` with the high-density sysctls. Same as `create bucket|iam|cloudsql`: the
snapshot and `-assets` buckets with the same grants, and Postgres 18 Enterprise with private IP, IAM
database auth, the `atepg` database and an `ate-api-server` service account.

Deliberate differences, all for cost or safety:

- `t2d-standard-4` Spot, one node with a 50 GB disk, no nested virtualization, instead of two
  `c3-standard-4` with 100 GB. We run gVisor workers only; micro-VM workers would need C3 and nested
  virtualization. T2D runs on a single CPU platform, so a snapshot taken on one node restores on any
  other.
- A dedicated `justpass` VPC instead of `default`. Nodes use their own service account and have no
  public IPs. The control plane keeps a public endpoint, guarded by Google IAM.
- Cloud SQL is `db-g1-small` (not `db-custom-2-8192`) without point-in-time recovery, and shares the
  instance with `app`. `db-f1-micro` is cheaper but has too few connections for Substrate.
- ate-api-server's Google service account also gets the snapshot bucket roles. Substrate's install
  annotates its Kubernetes service account with the Google one for Cloud SQL, so its GCS calls use
  the Google identity; setup-gcp grants only the Kubernetes one.
- A snapshot cleanup rule (setup-gcp has none), limited to actor snapshots so tags survive.
