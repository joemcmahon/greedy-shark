#!/bin/bash
# Greedy Shark Health Check Script
# Checks every container of the current compose project.
# Exit codes: 0 all running and healthy, 1 running but unhealthy, 2 any not running.
# Run from the project directory (or set COMPOSE_PROJECT_NAME).

project="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"

echo "🦈 Greedy Shark Health Check (project: $project)"
echo "============================="
echo ""

names=$(docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' | sort)

if [[ -z "$names" ]]; then
    echo "❌ No containers found for project '$project'!"
    exit 2
fi

not_running=0
unhealthy=0

for name in $names; do
    status=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo "not found")
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no healthcheck{{end}}' "$name" 2>/dev/null || echo "unknown")
    restarts=$(docker inspect -f '{{.RestartCount}}' "$name" 2>/dev/null || echo "0")

    echo "$name:"
    echo "  Status: $status"
    echo "  Health: $health"
    echo "  Restarts: $restarts"
    echo ""

    if [[ "$status" != "running" ]]; then
        not_running=1
    elif [[ "$health" != "healthy" ]]; then
        unhealthy=1
    fi
done

if [[ $not_running -eq 1 ]]; then
    echo "❌ One or more containers not running!"
    exit 2
elif [[ $unhealthy -eq 1 ]]; then
    echo "⚠️  Containers running but health check failing"
    exit 1
else
    echo "✅ All systems operational!"
    exit 0
fi
