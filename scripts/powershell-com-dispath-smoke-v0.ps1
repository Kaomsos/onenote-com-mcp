# powershell-com-dispatch-smoke-v0.ps1
#
# Read-only feasibility probe:
# - requires an already-visible OneNote Desktop window
# - creates one OneNote.Application COM client
# - processes two independent JSON requests through that same COM client
# - never writes, opens, closes, syncs, or deletes OneNote data
# - never includes hierarchy XML in a response, log, or file

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$InformationPreference = "SilentlyContinue"

function Format-HResult {
    param(
        [Parameter(Mandatory = $true)]
        [System.Exception]$Exception
    )

    $bytes = [System.BitConverter]::GetBytes([int]$Exception.HResult)
    $unsigned = [System.BitConverter]::ToUInt32($bytes, 0)
    return ("0x{0:X8}" -f $unsigned)
}

$summary = [ordered]@{
    probe                    = "powershell-com-dispatch-smoke-v0"
    ok                       = $false
    stage                    = "preflight"
    apartment                = [System.Threading.Thread]::CurrentThread.GetApartmentState().ToString()
    com_client_created       = $false
    com_client_creation_count = 0
    com_client_reused        = $false
    request_transport        = "in_process_json_loop"
    operation                = "get_hierarchy"
    hierarchy_scope          = 2
    xml_schema               = 2
    requested_invocations    = 2
    request_count            = 0
    response_count           = 0
    request_ids              = @()
    response_ids             = @()
    responses_correlated     = $false
    max_concurrent_com_calls = 0
    invocation_count         = 0
    completed_invocations    = 0
    invocation_elapsed_ms    = @()
    elapsed_ms               = $null
    host_pid                 = $PID
    release_attempted        = $false
    release_succeeded        = $false
    exception_type           = $null
    wrapper_hresult          = $null
    exception_depth          = 0
    leaf_exception_type      = $null
    hresult                  = $null
    category                 = $null
    error_id                 = $null
    powershell_version       = $PSVersionTable.PSVersion.ToString()
    is_64bit_process         = [Environment]::Is64BitProcess
}

$onenote = $null
$xml = $null

try {
    if ($summary.apartment -ne "STA") {
        throw [System.InvalidOperationException]::new(
            "Smoke test requires an STA PowerShell host."
        )
    }

    $visibleOneNote = @(
        Get-Process -Name "ONENOTE" -ErrorAction SilentlyContinue |
            Where-Object { [int64]$_.MainWindowHandle -ne 0 }
    )

    if ($visibleOneNote.Count -eq 0) {
        throw [System.InvalidOperationException]::new(
            "No visible OneNote Desktop window was detected."
        )
    }

    $summary.stage = "create_client"
    $onenote = New-Object -ComObject OneNote.Application
    $summary.com_client_created = $true
    $summary.com_client_creation_count = 1

    # Match the production constants exactly:
    # - HierarchyScope.hsNotebooks = 2
    # - XMLSchema.xs2013 = 2
    #
    # Keep these values distinct: HierarchyScope.hsPages is 4, but 4 is not
    # the XML schema used by the production bridge.
    # Serialize two independent requests before the dispatch loop so the probe
    # exercises the same data boundary a persistent host would receive. These
    # fixtures are fixed and contain no OneNote content or object identifiers.
    $requestPayloads = @(
        ([ordered]@{
            request_id = "request-1"
            operation = "get_hierarchy"
            params = [ordered]@{
                start_id = ""
                scope = $summary.hierarchy_scope
                schema = $summary.xml_schema
            }
        } | ConvertTo-Json -Compress -Depth 5),
        ([ordered]@{
            request_id = "request-2"
            operation = "get_hierarchy"
            params = [ordered]@{
                start_id = ""
                scope = $summary.hierarchy_scope
                schema = $summary.xml_schema
            }
        } | ConvertTo-Json -Compress -Depth 5)
    )

    # Process both JSON requests serially through the same $onenote instance.
    # Each returned XML value remains in memory only for its call and is never
    # copied into the content-free response JSON.
    $activeComCalls = 0
    $totalStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    for ($ordinal = 1; $ordinal -le $requestPayloads.Count; $ordinal += 1) {
        $summary.stage = "parse_request_$ordinal"
        $request = $requestPayloads[$ordinal - 1] | ConvertFrom-Json
        $summary.request_count += 1
        $summary.request_ids += [string]$request.request_id

        if ([string]$request.operation -ne "get_hierarchy") {
            throw [System.InvalidOperationException]::new(
                "Unsupported smoke-test operation."
            )
        }

        [string]$startId = $request.params.start_id
        [int]$scope = $request.params.scope
        [int]$schema = $request.params.schema
        if ($scope -ne $summary.hierarchy_scope -or $schema -ne $summary.xml_schema) {
            throw [System.InvalidOperationException]::new(
                "Smoke-test request constants do not match the production constants."
            )
        }

        $summary.stage = "invoke_get_hierarchy_$ordinal"
        [string]$xml = ""
        $summary.invocation_count += 1

        $invocationStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $activeComCalls += 1
        $summary.max_concurrent_com_calls = [Math]::Max(
            $summary.max_concurrent_com_calls,
            $activeComCalls
        )
        try {
            $onenote.GetHierarchy($startId, $scope, [ref]$xml, $schema)
        }
        finally {
            $activeComCalls -= 1
            $invocationStopwatch.Stop()
        }

        $elapsed = [Math]::Round(
            $invocationStopwatch.Elapsed.TotalMilliseconds,
            3
        )
        $summary.invocation_elapsed_ms += $elapsed
        $summary.completed_invocations += 1

        # Model the host response boundary without returning the hierarchy XML.
        $responseJson = [ordered]@{
            request_id = [string]$request.request_id
            operation = "get_hierarchy"
            ok = $true
            elapsed_ms = $elapsed
            result_shape = "xml_string"
        } | ConvertTo-Json -Compress -Depth 5
        $response = $responseJson | ConvertFrom-Json
        $summary.response_count += 1
        $summary.response_ids += [string]$response.request_id
        $xml = $null
    }
    $totalStopwatch.Stop()

    $summary.elapsed_ms = [Math]::Round($totalStopwatch.Elapsed.TotalMilliseconds, 3)
    $summary.responses_correlated = (
        ($summary.request_ids -join "|") -eq ($summary.response_ids -join "|")
    )
    $summary.com_client_reused = (
        $summary.com_client_creation_count -eq 1 -and
        $summary.completed_invocations -eq $summary.requested_invocations -and
        $summary.response_count -eq $summary.request_count -and
        $summary.responses_correlated -and
        $summary.max_concurrent_com_calls -eq 1
    )

    $summary.stage = "completed"
    $summary.ok = $true
}
catch {
    $exception = $_.Exception
    $leaf = $exception
    $depth = 0

    while ($null -ne $leaf.InnerException -and $depth -lt 8) {
        $leaf = $leaf.InnerException
        $depth += 1
    }

    $summary.ok = $false
    $summary.exception_type = $exception.GetType().FullName
    $summary.wrapper_hresult = Format-HResult $exception
    $summary.exception_depth = $depth
    $summary.leaf_exception_type = $leaf.GetType().FullName
    $summary.hresult = Format-HResult $leaf
    $summary.category = [string]$_.CategoryInfo.Category
    $summary.error_id = [string]$_.FullyQualifiedErrorId
}
finally {
    if ($null -ne $onenote) {
        $summary.release_attempted = $true

        try {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($onenote)
            $summary.release_succeeded = $true
        }
        catch {
            if ($summary.ok) {
                $exception = $_.Exception
                $summary.ok = $false
                $summary.stage = "release_client"
                $summary.exception_type = $exception.GetType().FullName
                $summary.wrapper_hresult = Format-HResult $exception
                $summary.exception_depth = 0
                $summary.leaf_exception_type = $exception.GetType().FullName
                $summary.hresult = Format-HResult $exception
                $summary.category = [string]$_.CategoryInfo.Category
                $summary.error_id = [string]$_.FullyQualifiedErrorId
            }
        }
    }
}

$summary | ConvertTo-Json -Compress

if ($summary.ok) {
    exit 0
}

exit 1
