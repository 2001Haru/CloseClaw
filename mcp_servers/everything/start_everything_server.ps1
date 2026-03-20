param(
    [string]$PackageVersion = "latest"
)

$ErrorActionPreference = "Stop"

if ($PackageVersion -eq "latest") {
    $pkg = "@modelcontextprotocol/server-everything"
} else {
    $pkg = "@modelcontextprotocol/server-everything@$PackageVersion"
}

# Use npx so the official Everything server can run without local npm install.
npx -y $pkg
