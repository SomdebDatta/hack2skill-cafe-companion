#!/usr/bin/env bash
# Deploy The Cafe Companion to Cloud Run.
#
#   ./deploy.sh              ship current code to the existing project (the normal case)
#   ./deploy.sh --bootstrap  create a NEW project first, then deploy
#
# The project below already exists and is linked to billing. Plain ./deploy.sh never
# creates cloud resources beyond the revision it ships.
set -euo pipefail

PROJECT="${PROJECT:-cafe-companion-515751}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-cafe-companion}"
# BILLING is only needed by --bootstrap; checked inside that branch so a plain
# redeploy does not require it. Find yours with: gcloud billing accounts list
BILLING="${BILLING:-}"

# The SDK was installed from Google's tarball (Homebrew's cask is broken), so it is not
# necessarily on PATH.
[ -d "$HOME/google-cloud-sdk/bin" ] && PATH="$HOME/google-cloud-sdk/bin:$PATH"

command -v gcloud >/dev/null || { echo "gcloud not found"; exit 1; }
gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q . \
  || { echo "Not authenticated. Run: gcloud auth login"; exit 1; }

# Key comes from .env at deploy time; never baked into the image or committed.
KEY="$(grep -E '^GEMINI_API_KEY=' .env 2>/dev/null | cut -d= -f2- | tr -d '"'"'"' ' || true)"
[ -z "$KEY" ] && { echo "No GEMINI_API_KEY in .env"; exit 1; }

if [ "${1:-}" = "--bootstrap" ]; then
  [ -z "$BILLING" ] && { echo "--bootstrap needs BILLING=<account-id> (gcloud billing accounts list)"; exit 1; }
  PROJECT="cafe-companion-$(date +%s | tail -c 7)"
  echo "▸ Creating project $PROJECT"
  gcloud projects create "$PROJECT" --name="Cafe Companion" --quiet
  gcloud billing projects link "$PROJECT" --billing-account="$BILLING" --quiet
  gcloud config set project "$PROJECT" --quiet
  echo "▸ Enabling APIs"
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
      artifactregistry.googleapis.com --quiet
  # IAM on a fresh project lags behind API enablement by a minute or two; gcloud would
  # otherwise fail to auto-create this repo with PERMISSION_DENIED despite roles/owner.
  echo "▸ Creating Artifact Registry repo (retrying while IAM propagates)"
  for i in $(seq 1 20); do
    gcloud artifacts repositories create cloud-run-source-deploy \
      --repository-format=docker --location="$REGION" --quiet 2>/tmp/ar.log && break
    grep -q "ALREADY_EXISTS\|already exists" /tmp/ar.log && break
    echo "   attempt $i failed, retrying in 20s"; sleep 20
  done
else
  gcloud config set project "$PROJECT" --quiet
fi

echo "▸ Deploying $SERVICE to $PROJECT ($REGION)"
# --max-instances=1 is a CORRECTNESS requirement, not a cost tweak: the room state is an
# in-memory singleton, so a second instance would serve a different queue and the staff
# board would disagree with the customer screen. Same reason uvicorn runs --workers 1.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --max-instances 1 \
  --min-instances 0 \
  --cpu 1 --memory 512Mi \
  --timeout 120 \
  --set-env-vars "GEMINI_API_KEY=$KEY" \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" \
        --region "$REGION" --format='value(status.url)')"
echo
echo "✅ Live:        $URL"
echo "   Staff board: $URL/staff"
echo "   Health:      $URL/api/health"
