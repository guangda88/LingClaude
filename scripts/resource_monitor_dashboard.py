"""Resource Allocation and Monitoring Dashboard.

This module provides a web dashboard for monitoring and allocating
resources across all Ling family projects.

Key Features:
1. Real-time resource monitoring (CPU, Memory, Bandwidth)
2. Manual traffic allocation adjustment
3. Request rate limiting configuration
4. Visual resource usage charts
5. Configuration persistence
"""
from __future__ import annotations

import json
import logging
import time
import psutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


@dataclass
class ProjectResourceConfig:
    """Resource configuration for a project."""

    name: str
    display_name: str
    priority: str  # high, medium, low
    cpu_quota: int  # percentage
    memory_quota_mb: int
    bandwidth_quota_mbps: int
    request_rate: int  # requests per minute
    enabled: bool = True


@dataclass
class ProjectResourceUsage:
    """Real-time resource usage for a project."""

    name: str
    pid: int | None = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    bandwidth_mbps: float = 0.0
    requests_per_minute: int = 0
    status: str = "unknown"  # running, stopped, unknown


@dataclass
class GlobalResourceLimits:
    """Global resource limits."""

    total_cpu_percent: int = 100
    total_memory_mb: int = 32 * 1024  # 32GB
    total_bandwidth_mbps: int = 1000  # 1Gbps


class ResourceMonitor:
    """Monitor and allocate resources across Ling family projects."""

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize resource monitor.

        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path or str(
            Path.home() / ".lingclaude" / "resource_monitor_config.json"
        )
        self.global_limits = GlobalResourceLimits()
        self.projects: Dict[str, ProjectResourceConfig] = {}
        self.usage: Dict[str, ProjectResourceUsage] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from file."""
        config_file = Path(self.config_path)
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for name, project_config in data.get("projects", {}).items():
                    self.projects[name] = ProjectResourceConfig(
                        name=project_config["name"],
                        display_name=project_config["display_name"],
                        priority=project_config["priority"],
                        cpu_quota=project_config["cpu_quota"],
                        memory_quota_mb=project_config["memory_quota_mb"],
                        bandwidth_quota_mbps=project_config["bandwidth_quota_mbps"],
                        request_rate=project_config["request_rate"],
                        enabled=project_config.get("enabled", True),
                    )
        else:
            # Initialize with default projects
            self._init_default_projects()

    def _init_default_projects(self) -> None:
        """Initialize with default Ling family projects."""
        default_projects = [
            ProjectResourceConfig(
                name="lingzhi",
                display_name="灵知",
                priority="high",
                cpu_quota=20,
                memory_quota_mb=7 * 1024,
                bandwidth_quota_mbps=20,
                request_rate=60,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingresearch",
                display_name="灵研",
                priority="high",
                cpu_quota=30,
                memory_quota_mb=11 * 1024,
                bandwidth_quota_mbps=30,
                request_rate=120,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingflow",
                display_name="灵通",
                priority="medium",
                cpu_quota=15,
                memory_quota_mb=4 * 1024,
                bandwidth_quota_mbps=15,
                request_rate=30,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingmessage",
                display_name="灵信",
                priority="high",
                cpu_quota=5,
                memory_quota_mb=512,
                bandwidth_quota_mbps=5,
                request_rate=10,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingminopt",
                display_name="灵极优",
                priority="medium",
                cpu_quota=10,
                memory_quota_mb=2 * 1024,
                bandwidth_quota_mbps=10,
                request_rate=20,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingterm",
                display_name="灵犀",
                priority="high",
                cpu_quota=10,
                memory_quota_mb=1 * 1024,
                bandwidth_quota_mbps=10,
                request_rate=10,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="zhibridge",
                display_name="智桥",
                priority="high",
                cpu_quota=5,
                memory_quota_mb=512,
                bandwidth_quota_mbps=5,
                request_rate=10,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingclaude",
                display_name="灵克",
                priority="low",
                cpu_quota=5,
                memory_quota_mb=512,
                bandwidth_quota_mbps=5,
                request_rate=5,
                enabled=True,
            ),
            ProjectResourceConfig(
                name="lingyang",
                display_name="灵扬",
                priority="low",
                cpu_quota=5,
                memory_quota_mb=512,
                bandwidth_quota_mbps=5,
                request_rate=5,
                enabled=True,
            ),
        ]

        for project in default_projects:
            self.projects[project.name] = project

    def _save_config(self) -> None:
        """Save configuration to file."""
        config_dir = Path(self.config_path).parent
        config_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "projects": {
                name: {
                    "name": project.name,
                    "display_name": project.display_name,
                    "priority": project.priority,
                    "cpu_quota": project.cpu_quota,
                    "memory_quota_mb": project.memory_quota_mb,
                    "bandwidth_quota_mbps": project.bandwidth_quota_mbps,
                    "request_rate": project.request_rate,
                    "enabled": project.enabled,
                }
                for name, project in self.projects.items()
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Configuration saved to {self.config_path}")

    def update_project_config(
        self,
        project_name: str,
        config: ProjectResourceConfig,
    ) -> None:
        """Update project configuration.

        Args:
            project_name: Name of the project
            config: New configuration
        """
        if project_name not in self.projects:
            raise HTTPException(
                status_code=404,
                detail=f"Project {project_name} not found",
            )

        self.projects[project_name] = config
        self._save_config()
        logger.info(f"Updated configuration for {project_name}")

    def get_project_config(self, project_name: str) -> ProjectResourceConfig:
        """Get project configuration.

        Args:
            project_name: Name of the project

        Returns:
            Project configuration
        """
        if project_name not in self.projects:
            raise HTTPException(
                status_code=404,
                detail=f"Project {project_name} not found",
            )

        return self.projects[project_name]

    def get_all_project_configs(self) -> Dict[str, ProjectResourceConfig]:
        """Get all project configurations.

        Returns:
            Dictionary of all project configurations
        """
        return self.projects

    def get_global_limits(self) -> GlobalResourceLimits:
        """Get global resource limits.

        Returns:
            Global resource limits
        """
        return self.global_limits

    def get_system_usage(self) -> Dict[str, float]:
        """Get current system resource usage.

        Returns:
            Dictionary of resource usage
        """
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        memory_mb = memory.used / (1024 * 1024)
        memory_percent = memory.percent

        # Bandwidth estimation (Linux specific)
        try:
            net_io = psutil.net_io_counters()
            # Simple estimation: current IO rate
            time.sleep(1)
            net_io_new = psutil.net_io_counters()
            bytes_sent = net_io_new.bytes_sent - net_io.bytes_sent
            bytes_recv = net_io_new.bytes_recv - net_io.bytes_recv
            total_bytes = bytes_sent + bytes_recv
            bandwidth_mbps = (total_bytes * 8) / (1024 * 1024)  # Mbps
        except Exception:
            bandwidth_mbps = 0.0

        return {
            "cpu_percent": cpu_percent,
            "memory_mb": memory_mb,
            "memory_percent": memory_percent,
            "bandwidth_mbps": bandwidth_mbps,
        }

    def get_resource_allocation_status(self) -> Dict[str, object]:
        """Get resource allocation status.

        Returns:
            Dictionary with allocation status
        """
        # Calculate allocated resources
        allocated_cpu = sum(
            p.cpu_quota for p in self.projects.values() if p.enabled
        )
        allocated_memory_mb = sum(
            p.memory_quota_mb for p in self.projects.values() if p.enabled
        )
        allocated_bandwidth_mbps = sum(
            p.bandwidth_quota_mbps for p in self.projects.values() if p.enabled
        )

        # Get system usage
        system_usage = self.get_system_usage()

        return {
            "allocated": {
                "cpu_percent": allocated_cpu,
                "memory_mb": allocated_memory_mb,
                "bandwidth_mbps": allocated_bandwidth_mbps,
            },
            "limits": {
                "cpu_percent": self.global_limits.total_cpu_percent,
                "memory_mb": self.global_limits.total_memory_mb,
                "bandwidth_mbps": self.global_limits.total_bandwidth_mbps,
            },
            "usage": system_usage,
            "utilization": {
                "cpu_percent": (allocated_cpu / self.global_limits.total_cpu_percent * 100),
                "memory_percent": (allocated_memory_mb / self.global_limits.total_memory_mb * 100),
                "bandwidth_percent": (
                    allocated_bandwidth_mbps / self.global_limits.total_bandwidth_mbps * 100
                ),
            },
        }


# FastAPI application
app = FastAPI(
    title="灵字辈资源分配监控面板",
    description="Resource Allocation and Monitoring Dashboard for Ling Family Projects",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize resource monitor
monitor = ResourceMonitor()

# Templates
templates = Jinja2Templates(directory="templates")


@app.get("/")
async def dashboard(request: Request) -> HTMLResponse:
    """Serve main dashboard page.

    Args:
        request: FastAPI request

    Returns:
        HTML dashboard page
    """
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/projects")
async def get_projects() -> JSONResponse:
    """Get all project configurations.

    Returns:
        JSON response with project configurations
    """
    projects = monitor.get_all_project_configs()
    return JSONResponse(
        {
            "projects": {
                name: {
                    "name": project.name,
                    "display_name": project.display_name,
                    "priority": project.priority,
                    "cpu_quota": project.cpu_quota,
                    "memory_quota_mb": project.memory_quota_mb,
                    "bandwidth_quota_mbps": project.bandwidth_quota_mbps,
                    "request_rate": project.request_rate,
                    "enabled": project.enabled,
                }
                for name, project in projects.items()
            }
        }
    )


@app.get("/api/projects/{project_name}")
async def get_project(project_name: str) -> JSONResponse:
    """Get specific project configuration.

    Args:
        project_name: Name of the project

    Returns:
        JSON response with project configuration
    """
    project = monitor.get_project_config(project_name)
    return JSONResponse(
        {
            "name": project.name,
            "display_name": project.display_name,
            "priority": project.priority,
            "cpu_quota": project.cpu_quota,
            "memory_quota_mb": project.memory_quota_mb,
            "bandwidth_quota_mbps": project.bandwidth_quota_mbps,
            "request_rate": project.request_rate,
            "enabled": project.enabled,
        }
    )


@app.put("/api/projects/{project_name}")
async def update_project(
    project_name: str,
    config: dict,
) -> JSONResponse:
    """Update project configuration.

    Args:
        project_name: Name of the project
        config: New configuration

    Returns:
        JSON response with updated configuration
    """
    project_config = ProjectResourceConfig(
        name=config["name"],
        display_name=config["display_name"],
        priority=config["priority"],
        cpu_quota=config["cpu_quota"],
        memory_quota_mb=config["memory_quota_mb"],
        bandwidth_quota_mbps=config["bandwidth_quota_mbps"],
        request_rate=config["request_rate"],
        enabled=config.get("enabled", True),
    )

    monitor.update_project_config(project_name, project_config)

    return JSONResponse(
        {
            "name": project_config.name,
            "display_name": project_config.display_name,
            "priority": project_config.priority,
            "cpu_quota": project_config.cpu_quota,
            "memory_quota_mb": project_config.memory_quota_mb,
            "bandwidth_quota_mbps": project_config.bandwidth_quota_mbps,
            "request_rate": project_config.request_rate,
            "enabled": project_config.enabled,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Get resource allocation status.

    Returns:
        JSON response with status
    """
    status = monitor.get_resource_allocation_status()
    return JSONResponse(status)


@app.get("/api/system")
async def get_system() -> JSONResponse:
    """Get system resource usage.

    Returns:
        JSON response with system usage
    """
    system_usage = monitor.get_system_usage()
    return JSONResponse(system_usage)


if __name__ == "__main__":
    # Create templates directory
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)

    # Create simple HTML template
    index_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>灵字辈资源分配监控面板</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        .header p {
            font-size: 1.1em;
            opacity: 0.9;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
            padding: 30px;
        }
        .card {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
            transition: transform 0.3s, box-shadow 0.3s;
        }
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .card-title {
            font-size: 1.5em;
            font-weight: 600;
            color: #2c3e50;
        }
        .priority-badge {
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
            font-weight: 600;
        }
        .priority-high {
            background: #e74c3c;
            color: white;
        }
        .priority-medium {
            background: #f39c12;
            color: white;
        }
        .priority-low {
            background: #27ae60;
            color: white;
        }
        .status-toggle {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 26px;
        }
        .status-toggle input {
            opacity: 0;
            width: 0;
            height: 0;
        }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: .4s;
            border-radius: 26px;
        }
        .slider:before {
            position: absolute;
            content: "";
            height: 20px;
            width: 20px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .4s;
            border-radius: 50%;
        }
        input:checked + .slider {
            background-color: #2196F3;
        }
        input:checked + .slider:before {
            transform: translateX(24px);
        }
        .resource-item {
            margin-bottom: 15px;
        }
        .resource-label {
            font-size: 0.95em;
            color: #7f8c8d;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
        }
        .resource-value {
            font-weight: 600;
            color: #2c3e50;
        }
        .resource-bar {
            height: 8px;
            background: #e0e0e0;
            border-radius: 4px;
            overflow: hidden;
            position: relative;
        }
        .resource-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            border-radius: 4px;
            transition: width 0.3s ease;
        }
        .control-slider {
            width: 100%;
            margin: 10px 0;
        }
        .overall-stats {
            grid-column: 1 / -1;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-top: 20px;
        }
        .stat-item {
            text-align: center;
        }
        .stat-value {
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 5px;
        }
        .stat-label {
            font-size: 1em;
            opacity: 0.9;
        }
        .progress-circle {
            width: 80px;
            height: 80px;
            margin: 0 auto 10px;
            position: relative;
        }
        .save-button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 25px;
            font-size: 1em;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
            margin-top: 20px;
        }
        .save-button:hover {
            transform: scale(1.05);
        }
        @media (max-width: 768px) {
            .dashboard {
                grid-template-columns: 1fr;
            }
            .stats-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎯 灵字辈资源分配监控面板</h1>
            <p>实时监控和手动调节流量分配与请求频率</p>
        </div>

        <div class="overall-stats">
            <h2>📊 整体资源状态</h2>
            <div class="stats-grid">
                <div class="stat-item">
                    <div class="stat-value" id="overall-cpu">0%</div>
                    <div class="stat-label">CPU 使用率</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="overall-memory">0 GB</div>
                    <div class="stat-label">内存使用</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="overall-bandwidth">0 Mbps</div>
                    <div class="stat-label">带宽使用</div>
                </div>
            </div>
        </div>

        <div class="dashboard" id="project-cards">
            <!-- Project cards will be dynamically inserted here -->
        </div>
    </div>

    <script>
        // API configuration
        const API_BASE = '/api';

        // Fetch all projects
        async function fetchProjects() {
            try {
                const response = await fetch(`${API_BASE}/projects`);
                const data = await response.json();
                renderProjects(data.projects);
            } catch (error) {
                console.error('Failed to fetch projects:', error);
            }
        }

        // Fetch system status
        async function fetchStatus() {
            try {
                const response = await fetch(`${API_BASE}/status`);
                const status = await response.json();

                // Update overall stats
                document.getElementById('overall-cpu').textContent = `${status.usage.cpu_percent.toFixed(1)}%`;
                document.getElementById('overall-memory').textContent = `${(status.usage.memory_mb / 1024).toFixed(1)} GB`;
                document.getElementById('overall-bandwidth').textContent = `${status.usage.bandwidth_mbps.toFixed(1)} Mbps`;

            } catch (error) {
                console.error('Failed to fetch status:', error);
            }
        }

        // Render projects
        function renderProjects(projects) {
            const container = document.getElementById('project-cards');
            container.innerHTML = '';

            for (const [name, project] of Object.entries(projects)) {
                const card = document.createElement('div');
                card.className = 'card';
                card.innerHTML = `
                    <div class="card-header">
                        <div class="card-title">${project.display_name}</div>
                        <div>
                            <span class="priority-badge priority-${project.priority}">${project.priority.toUpperCase()}</span>
                            <label class="status-toggle">
                                <input type="checkbox" ${project.enabled ? 'checked' : ''} onchange="toggleProject('${name}', this.checked)">
                                <span class="slider"></span>
                            </label>
                        </div>
                    </div>

                    <div class="resource-item">
                        <div class="resource-label">
                            <span>CPU 配额</span>
                            <span class="resource-value">${project.cpu_quota}%</span>
                        </div>
                        <div class="resource-bar">
                            <div class="resource-bar-fill" style="width: ${project.cpu_quota}%"></div>
                        </div>
                        <input type="range" class="control-slider" min="0" max="50" value="${project.cpu_quota}"
                            onchange="updateResource('${name}', 'cpu_quota', this.value)">
                    </div>

                    <div class="resource-item">
                        <div class="resource-label">
                            <span>内存配额</span>
                            <span class="resource-value">${(project.memory_quota_mb / 1024).toFixed(1)} GB</span>
                        </div>
                        <div class="resource-bar">
                            <div class="resource-bar-fill" style="width: ${(project.memory_quota_mb / (32 * 1024) * 100)}%"></div>
                        </div>
                        <input type="range" class="control-slider" min="0" max="16" value="${(project.memory_quota_mb / 1024).toFixed(1)}"
                            onchange="updateResource('${name}', 'memory_quota_mb', this.value * 1024)">
                    </div>

                    <div class="resource-item">
                        <div class="resource-label">
                            <span>带宽配额</span>
                            <span class="resource-value">${project.bandwidth_quota_mbps} Mbps</span>
                        </div>
                        <div class="resource-bar">
                            <div class="resource-bar-fill" style="width: ${(project.bandwidth_quota_mbps / 1000 * 100)}%"></div>
                        </div>
                        <input type="range" class="control-slider" min="0" max="200" value="${project.bandwidth_quota_mbps}"
                            onchange="updateResource('${name}', 'bandwidth_quota_mbps', this.value)">
                    </div>

                    <div class="resource-item">
                        <div class="resource-label">
                            <span>请求频率</span>
                            <span class="resource-value">${project.request_rate} 次/分钟</span>
                        </div>
                        <div class="resource-bar">
                            <div class="resource-bar-fill" style="width: ${(project.request_rate / 120 * 100)}%"></div>
                        </div>
                        <input type="range" class="control-slider" min="0" max="120" value="${project.request_rate}"
                            onchange="updateResource('${name}', 'request_rate', this.value)">
                    </div>
                `;
                container.appendChild(card);
            }
        }

        // Toggle project
        async function toggleProject(name, enabled) {
            try {
                const response = await fetch(`${API_BASE}/projects/${name}`);
                const config = await response.json();
                config.enabled = enabled;

                const updateResponse = await fetch(`${API_BASE}/projects/${name}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(config),
                });

                if (updateResponse.ok) {
                    console.log(`Project ${name} ${enabled ? 'enabled' : 'disabled'}`);
                }
            } catch (error) {
                console.error('Failed to toggle project:', error);
            }
        }

        // Update resource
        async function updateResource(name, resource, value) {
            try {
                const response = await fetch(`${API_BASE}/projects/${name}`);
                const config = await response.json();
                config[resource] = parseInt(value);

                const updateResponse = await fetch(`${API_BASE}/projects/${name}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(config),
                });

                if (updateResponse.ok) {
                    console.log(`Updated ${resource} for ${name} to ${value}`);
                    // Re-render to show updated values
                    fetchProjects();
                }
            } catch (error) {
                console.error('Failed to update resource:', error);
            }
        }

        // Initialize
        fetchProjects();
        fetchStatus();

        // Refresh status every 5 seconds
        setInterval(fetchStatus, 5000);
    </script>
</body>
</html>
    """

    with open(templates_dir / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

    # Mount templates
    app.mount("/templates", StaticFiles(directory=str(templates_dir)), name="templates")

    logger.info("Starting Resource Allocation and Monitoring Dashboard...")
    logger.info(f"Monitoring {len(monitor.projects)} projects")

    # Run the server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8090,
        log_level="info",
    )
