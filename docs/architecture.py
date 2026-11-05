# /// script
# requires-python = ">=3.12"
# dependencies = ["diagrams==0.25.1"]
# ///
"""Render the portable Kubernetes architecture. Run: uv run --script docs/architecture.py.

Requires Graphviz on PATH. Generates only docs/architecture.png; no cloud calls.
"""
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.k8s.compute import Pod
from diagrams.k8s.network import Ing, SVC
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.network import Internet


def main():
    output = Path(__file__).resolve().with_suffix("")
    with Diagram(
        "Jira Relay | API + worker + PostgreSQL",
        filename=str(output),
        outformat="png",
        show=False,
        direction="LR",
        graph_attr={
            "fontname": "Helvetica",
            "fontsize": "24",
            "dpi": "140",
            "pad": "0.4",
            "nodesep": "0.8",
            "ranksep": "1.0",
            "splines": "spline",
            "bgcolor": "white",
        },
        node_attr={"fontname": "Helvetica", "fontsize": "14"},
        edge_attr={"fontname": "Helvetica", "fontsize": "12", "color": "#52637A"},
    ):
        source = Internet("Jira Cloud\nWebhooks firmados")
        database = PostgreSQL("PostgreSQL externo\nEventos y reglas\nEjecuciones y cola")
        providers = Internet("APIs externas\nJira / Bitbucket")

        with Cluster("Kubernetes | namespace de la aplicación", graph_attr={"bgcolor": "#F0F5FC"}):
            ingress = Ing("Ingress HTTPS\n/webhooks/jira")
            service = SVC("Service API\nHacia puerto 8080")
            with Cluster("Deployment API", graph_attr={"bgcolor": "#E1EDFF", "margin": "24"}):
                api = Pod("Pod API\nContenedor API :8080")
            with Cluster("Deployment worker", graph_attr={"bgcolor": "#E5F4EC", "margin": "24"}):
                worker = Pod("Pod worker\nContenedor worker")

        source >> Edge(label="HTTPS") >> ingress >> service >> api
        # Layout only: there is no Service or network hop to the worker.
        service >> Edge(style="invis") >> worker
        api >> Edge(label="Persiste antes del 202") >> database
        worker >> Edge(label="Toma trabajo / guarda resultados", dir="both") >> database
        worker >> Edge(label="Acciones y pipelines") >> providers


if __name__ == "__main__":
    main()
