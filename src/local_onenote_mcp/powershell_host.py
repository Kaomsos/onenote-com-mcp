"""Fixed Windows PowerShell fragments for OneNote COM adapters.

User data is never interpolated into these constants. One-shot and persistent
hosts are assembled from the same operation switch and error helpers.
"""

from __future__ import annotations

import base64
import textwrap

# Match CopyBudget max_total_xml_bytes plus a small envelope allowance.
MAX_DECODED_FRAME_BYTES = 256 * 1024 * 1024 + 256 * 1024
MAX_ENCODED_FRAME_BYTES = (MAX_DECODED_FRAME_BYTES * 4 // 3) + 4096
FRAME_PREFIX_TEXT = "ONB1 "


POWERSHELL_OPERATION_NAMES: frozenset[str] = frozenset(
    {
        "get_hierarchy",
        "open_hierarchy",
        "open_hierarchy_batch",
        "update_hierarchy",
        "delete_hierarchy",
        "close_notebook",
        "get_hierarchy_parent",
        "get_special_location",
        "create_new_page",
        "get_page_content",
        "update_page_content",
        "delete_page_content",
        "get_binary_page_content",
        "publish",
        "find_pages",
        "find_meta",
        "get_hyperlink",
        "get_web_hyperlink",
        "navigate_to",
        "navigate_to_url",
        "sync_hierarchy",
        "merge_sections",
        "set_filing_location",
    }
)

POWERSHELL_ERROR_HELPERS = (
    "function New-Ok($data){return @{ok=$true;data=$data;error=$null}}\n"
    "function New-Err($err){$ex=$err.Exception;$leaf=$ex;$exceptionDepth=0;"
    "while($null-ne$leaf.InnerException-and$exceptionDepth -lt 8){"
    "$leaf=$leaf.InnerException;$exceptionDepth+=1};"
    "return @{ok=$false;data=$null;error=@{message=$ex.Message;"
    "hresult = $leaf.HResult;wrapper_hresult = $ex.HResult;"
    "exception_depth=$exceptionDepth;leaf_exception_type=$leaf.GetType().FullName;"
    "category=[string]$err.CategoryInfo.Category}}}\n"
)

POWERSHELL_OPERATION_SWITCH = textwrap.dedent(r'''
        "get_hierarchy" {
            $xml = ""
            $onenote.GetHierarchy([string]$p.start_id, [int]$p.scope, [ref]$xml, [int]$p.schema)
            $data = @{ xml = $xml }
        }
        "open_hierarchy" {
            $objectId = ""
            $onenote.OpenHierarchy([string]$p.path, [string]$p.relative_to_id, [ref]$objectId, [int]$p.create_file_type)
            $data = @{ object_id = $objectId }
        }
        "open_hierarchy_batch" {
            $openedByKey = @{}
            $items = @()
            foreach ($entry in @($p.requests)) {
                $key = [string]$entry.key
                $relativeToId = [string]$entry.relative_to_id
                $parentKey = [string]$entry.parent_key
                try {
                    if (-not [string]::IsNullOrWhiteSpace($parentKey)) {
                        if (-not $openedByKey.ContainsKey($parentKey)) {
                            throw "Batch hierarchy parent key was not opened: $parentKey"
                        }
                        $relativeToId = [string]$openedByKey[$parentKey]
                    }
                    $objectId = ""
                    $onenote.OpenHierarchy(
                        [string]$entry.path,
                        $relativeToId,
                        [ref]$objectId,
                        [int]$entry.create_file_type
                    )
                    $openedByKey[$key] = $objectId
                    $items += @{
                        key = $key
                        ok = $true
                        object_id = $objectId
                        relative_to_id = $relativeToId
                        error = $null
                    }
                } catch {
                    $failure = New-Err $_
                    $items += @{
                        key = $key
                        ok = $false
                        object_id = $null
                        relative_to_id = $relativeToId
                        error = $failure.error
                    }
                }
            }
            $xml = $null
            $hierarchyError = $null
            try {
                $observedXml = ""
                $onenote.GetHierarchy(
                    [string]$p.notebook_id,
                    [int]$p.scope,
                    [ref]$observedXml,
                    [int]$p.schema
                )
                $xml = $observedXml
            } catch {
                $failure = New-Err $_
                $hierarchyError = $failure.error
            }
            $data = @{
                items = $items
                xml = $xml
                hierarchy_error = $hierarchyError
            }
        }
        "update_hierarchy" {
            $onenote.UpdateHierarchy([string]$p.xml, [int]$p.schema)
            $data = @{ updated = $true }
        }
        "delete_hierarchy" {
            $onenote.DeleteHierarchy([string]$p.object_id, 0, [bool]$p.permanently)
            $data = @{ deleted = $true }
        }
        "close_notebook" {
            $onenote.CloseNotebook([string]$p.notebook_id, [bool]$p.force)
            $data = @{ closed = $true }
        }
        "get_hierarchy_parent" {
            $parentId = ""
            $onenote.GetHierarchyParent([string]$p.object_id, [ref]$parentId)
            $data = @{ parent_id = $parentId }
        }
        "get_special_location" {
            $location = ""
            $onenote.GetSpecialLocation([int]$p.location, [ref]$location)
            $data = @{ path = $location }
        }
        "create_new_page" {
            $pageId = ""
            $onenote.CreateNewPage([string]$p.section_id, [ref]$pageId, [int]$p.new_page_style)
            $data = @{ page_id = $pageId }
        }
        "get_page_content" {
            $xml = ""
            $onenote.GetPageContent([string]$p.page_id, [ref]$xml, [int]$p.page_info, [int]$p.schema)
            $data = @{ xml = $xml }
        }
        "update_page_content" {
            $onenote.UpdatePageContent([string]$p.xml, 0, [int]$p.schema, [bool]$p.force)
            $data = @{ updated = $true }
        }
        "delete_page_content" {
            $onenote.DeletePageContent([string]$p.page_id, [string]$p.object_id, 0, [bool]$p.force)
            $data = @{ deleted = $true }
        }
        "get_binary_page_content" {
            $content = ""
            $onenote.GetBinaryPageContent([string]$p.page_id, [string]$p.callback_id, [ref]$content)
            $data = @{ base64 = $content }
        }
        "publish" {
            $onenote.Publish([string]$p.object_id, [string]$p.target_path, [int]$p.format, "")
            $data = @{ path = [string]$p.target_path }
        }
        "find_pages" {
            $xml = ""
            $onenote.FindPages([string]$p.start_id, [string]$p.query, [ref]$xml, [bool]$p.include_unindexed, [bool]$p.display, [int]$p.schema)
            $data = @{ xml = $xml }
        }
        "find_meta" {
            $xml = ""
            $onenote.FindMeta([string]$p.start_id, [string]$p.name, [ref]$xml, [bool]$p.include_unindexed, [int]$p.schema)
            $data = @{ xml = $xml }
        }
        "get_hyperlink" {
            $link = ""
            $onenote.GetHyperlinkToObject([string]$p.object_id, [string]$p.page_content_object_id, [ref]$link)
            $data = @{ hyperlink = $link }
        }
        "get_web_hyperlink" {
            $link = ""
            $onenote.GetWebHyperlinkToObject([string]$p.object_id, [string]$p.page_content_object_id, [ref]$link)
            $data = @{ hyperlink = $link }
        }
        "navigate_to" {
            $onenote.NavigateTo([string]$p.object_id, [string]$p.page_content_object_id, [bool]$p.new_window)
            $data = @{ navigated = $true }
        }
        "navigate_to_url" {
            $onenote.NavigateToUrl([string]$p.url, [bool]$p.new_window)
            $data = @{ navigated = $true }
        }
        "sync_hierarchy" {
            $onenote.SyncHierarchy([string]$p.object_id)
            $data = @{ synced = $true }
        }
        "merge_sections" {
            $onenote.MergeSections([string]$p.source_section_id, [string]$p.destination_section_id)
            $data = @{ merged = $true }
        }
        "set_filing_location" {
            $onenote.SetFilingLocation([int]$p.filing_location, [int]$p.filing_location_type, [string]$p.section_or_page_id)
            $data = @{ updated = $true }
        }
        default {
            throw "Unsupported OneNote bridge operation: $op"
        }
''')

POWERSHELL_ONE_SHOT_SCRIPT = (
    '$ErrorActionPreference = "Stop"\n'
    + POWERSHELL_ERROR_HELPERS
    + r'''
try {
    $requestPath = $env:LOCAL_ONENOTE_MCP_REQUEST
    $responsePath = $env:LOCAL_ONENOTE_MCP_RESPONSE
    if ([string]::IsNullOrWhiteSpace($requestPath) -or [string]::IsNullOrWhiteSpace($responsePath)) {
        throw "Bridge request/response paths are not set."
    }

    $request = Get-Content -LiteralPath $requestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $op = [string]$request.operation
    $p = $request.params
    $onenote = New-Object -ComObject OneNote.Application
    $data = $null

    switch ($op) {
'''
    + POWERSHELL_OPERATION_SWITCH
    + r'''
    }

    $response = New-Ok $data
} catch {
    $response = New-Err $_
}

$response | ConvertTo-Json -Depth 100 -Compress | Set-Content -LiteralPath $env:LOCAL_ONENOTE_MCP_RESPONSE -Encoding UTF8
'''
)

def _positive_frame_limit(value: int, name: str) -> int:
    number = int(value)
    if number < 1:
        raise ValueError(f"{name} must be a positive integer")
    return number


def _host_allowlist_script() -> str:
    names = ",".join(sorted(POWERSHELL_OPERATION_NAMES))
    return f"$script:Allowed='{names}'\n"


def _host_framing_script(
    *,
    max_decoded_frame_bytes: int,
    max_encoded_frame_bytes: int,
) -> str:
    max_decoded = _positive_frame_limit(max_decoded_frame_bytes, "max_decoded_frame_bytes")
    max_encoded = _positive_frame_limit(max_encoded_frame_bytes, "max_encoded_frame_bytes")
    proto = "protocol_violation"
    prefix = FRAME_PREFIX_TEXT
    return f'''
$ProgressPreference="SilentlyContinue";$InformationPreference="SilentlyContinue";$WarningPreference="SilentlyContinue"
$script:MaxDecodedFrameBytes={max_decoded};$script:MaxEncodedFrameBytes={max_encoded}
$script:In=$null;$script:Buf=New-Object Collections.Generic.List[byte]
function Get-RequiredJsonInt($o,$n){{$p=$o.PSObject.Properties[$n];if($null-eq$p){{throw "{proto}"}};$v=$p.Value;if($v-is[bool]){{throw "{proto}"}};if($v-is[int]-or$v-is[long]-or$v-is[int64]){{return [int]$v}};throw "{proto}"}}
function Get-RequiredJsonString($o,$n){{$p=$o.PSObject.Properties[$n];if($null-eq$p){{throw "{proto}"}};$v=$p.Value;if($v-isnot[string]){{throw "{proto}"}};return [string]$v}}
function Get-RequiredJsonObject($o,$n){{$p=$o.PSObject.Properties[$n];if($null-eq$p){{throw "{proto}"}};$v=$p.Value;if($null-eq$v-or$v-is[string]-or$v-is[ValueType]-or$v-is[Array]){{throw "{proto}"}};return $v}}
function Write-HostFrame($payload){{$json=$payload|ConvertTo-Json -Depth 100 -Compress;$b=[Text.Encoding]::UTF8.GetBytes([string]$json);if($b.Length-gt$script:MaxDecodedFrameBytes){{throw "{proto}"}};$e=[Convert]::ToBase64String($b);if($e.Length-gt$script:MaxEncodedFrameBytes){{throw "{proto}"}};[Console]::Out.WriteLine("{prefix}$e");[Console]::Out.Flush()}}
function Convert-HostLine($buf){{$len=$buf.Count;if($len-gt0-and$buf[$len-1]-eq13){{$len=$len-1}};$pre=[Text.Encoding]::ASCII.GetBytes("{prefix}");if($len-lt$pre.Length){{throw "{proto}"}};for($i=0;$i-lt$pre.Length;$i++){{if($buf[$i]-ne$pre[$i]){{throw "{proto}"}}}};$n=$len-$pre.Length;if($n-gt$script:MaxEncodedFrameBytes){{throw "{proto}"}};$raw=New-Object byte[] $n;for($i=0;$i-lt$n;$i++){{$raw[$i]=$buf[$pre.Length+$i]}};try{{$d=[Convert]::FromBase64String([Text.Encoding]::ASCII.GetString($raw))}}catch{{throw "{proto}"}};if($d.Length-gt$script:MaxDecodedFrameBytes){{throw "{proto}"}};try{{$obj=[Text.Encoding]::UTF8.GetString($d)|ConvertFrom-Json}}catch{{throw "{proto}"}};if($null-eq$obj-or$obj-is[Array]-or$obj-is[string]-or$obj-is[ValueType]){{throw "{proto}"}};return $obj}}
function Read-HostFrame{{if($null-eq$script:In){{$script:In=[Console]::OpenStandardInput()}};$max=$script:MaxEncodedFrameBytes+5;$c=New-Object byte[] 4096;while($true){{$nl=-1;for($i=0;$i-lt$script:Buf.Count;$i++){{if($script:Buf[$i]-eq10){{$nl=$i;break}}}};if($nl-ge0){{if($nl-gt$max){{throw "{proto}"}};$line=New-Object Collections.Generic.List[byte];for($i=0;$i-lt$nl;$i++){{[void]$line.Add($script:Buf[$i])}};$r=New-Object Collections.Generic.List[byte];for($i=$nl+1;$i-lt$script:Buf.Count;$i++){{[void]$r.Add($script:Buf[$i])}};$script:Buf=$r;return Convert-HostLine $line}};if($script:Buf.Count-gt$max){{throw "{proto}"}};$n=$script:In.Read($c,0,$c.Length);if($n-le0){{if($script:Buf.Count-eq0){{return $null}};throw "{proto}"}};for($i=0;$i-lt$n;$i++){{[void]$script:Buf.Add($c[$i])}}}}}}
'''

POWERSHELL_PRODUCTION_CLIENT_BOOTSTRAP = r'''
$onenote = New-Object -ComObject OneNote.Application
'''

POWERSHELL_FAKE_CLIENT_BOOTSTRAP = r'''
$onenote = $null
function Invoke-FakeBridgeOperation($op, $p) {
    if ($null -ne $p -and $p.PSObject.Properties["force_hresult"]) {
        $code = [int]$p.force_hresult
        throw (New-Object System.Runtime.InteropServices.COMException("fake com", $code))
    }
    if ($null -ne $p -and $p.PSObject.Properties["force_oversize"]) {
        $n = [int]$p.force_oversize
        if ($n -lt 1) { $n = 1 }
        return @{ xml = ("x" * $n) }
    }
    if ($op -eq "get_hierarchy") {
        return @{ xml = ("<one:Notebooks>" + [string]$p.start_id + "</one:Notebooks>") }
    }
    if ($op -eq "create_new_page") {
        return @{ page_id = "page-1" }
    }
    if ($op -eq "update_page_content") {
        return @{ updated = $true }
    }
    throw "Unsupported OneNote bridge operation: $op"
}
'''

POWERSHELL_HOST_LOOP = r'''
$script:ExpectedSequence=1;$script:ExpectedGeneration=$null
try{while($true){
$request=Read-HostFrame
if($null-eq$request){break}
if((Get-RequiredJsonInt $request "protocol_version")-ne1){throw "protocol_violation"}
$generation=Get-RequiredJsonInt $request "generation"
$sequence=Get-RequiredJsonInt $request "sequence"
$kind=Get-RequiredJsonString $request "kind"
if($null-eq$script:ExpectedGeneration){$script:ExpectedGeneration=$generation}elseif($generation-ne$script:ExpectedGeneration){throw "protocol_violation"}
if($kind-eq"shutdown"){if($sequence-ne$script:ExpectedSequence){throw "protocol_violation"};break}
if($kind-ne"request"-or$sequence-ne$script:ExpectedSequence){throw "protocol_violation"}
$op=Get-RequiredJsonString $request "operation"
if((","+$script:Allowed+",")-cnotlike("*,"+$op+",*")){throw "protocol_violation"}
$p=Get-RequiredJsonObject $request "params"
$script:ExpectedSequence+=1
$data=$null
try{
'''

POWERSHELL_HOST_LOOP_PRODUCTION_DISPATCH = r'''
            switch ($op) {
'''

POWERSHELL_HOST_LOOP_FAKE_DISPATCH = r'''
            $data = Invoke-FakeBridgeOperation $op $p
'''

POWERSHELL_HOST_LOOP_TAIL = r'''
$result=New-Ok $data}catch{$result=New-Err $_}
Write-HostFrame @{protocol_version=1;generation=$generation;sequence=$sequence;kind="response";ok=$result.ok;data=$result.data;error=$result.error}
}}finally{if($null-ne$onenote){try{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($onenote)}catch{}}}
'''


def assemble_persistent_host_script(
    *,
    fake_client: bool = False,
    max_decoded_frame_bytes: int = MAX_DECODED_FRAME_BYTES,
    max_encoded_frame_bytes: int = MAX_ENCODED_FRAME_BYTES,
) -> str:
    """Assemble the STA host script. Production never includes the fake client."""

    parts = [
        '$ErrorActionPreference = "Stop"\n',
        POWERSHELL_ERROR_HELPERS,
        _host_framing_script(
            max_decoded_frame_bytes=max_decoded_frame_bytes,
            max_encoded_frame_bytes=max_encoded_frame_bytes,
        ),
        _host_allowlist_script(),
        r'''
$apartment=[Threading.Thread]::CurrentThread.GetApartmentState().ToString()
if($apartment-ne"STA"){Write-HostFrame @{protocol_version=1;generation=0;sequence=0;kind="fatal"};exit 1}
''',
    ]
    if fake_client:
        parts.append(POWERSHELL_FAKE_CLIENT_BOOTSTRAP)
    else:
        parts.append(
            "try{"
            + POWERSHELL_PRODUCTION_CLIENT_BOOTSTRAP
            + "\n}catch{Write-HostFrame @{protocol_version=1;generation=0;sequence=0;kind=\"fatal\"};exit 1}\n"
        )
    parts.append(
        'Write-HostFrame @{protocol_version=1;generation=0;sequence=0;kind="ready";adapter="persistent_powershell";pid=$PID;apartment=$apartment}\n'
    )
    parts.append(POWERSHELL_HOST_LOOP)
    if fake_client:
        parts.append(POWERSHELL_HOST_LOOP_FAKE_DISPATCH)
    else:
        parts.append(POWERSHELL_HOST_LOOP_PRODUCTION_DISPATCH)
        parts.append(POWERSHELL_OPERATION_SWITCH)
        parts.append("            }\n")
    parts.append(POWERSHELL_HOST_LOOP_TAIL)
    return "".join(parts)


POWERSHELL_PERSISTENT_HOST_SCRIPT = assemble_persistent_host_script(fake_client=False)
POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT = assemble_persistent_host_script(fake_client=True)


def encode_powershell_command(script: str) -> str:
    """Encode a PowerShell script for ``-EncodedCommand`` (UTF-16LE then Base64)."""

    return base64.b64encode(script.encode("utf-16le")).decode("ascii")
