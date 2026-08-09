[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExpectedPageId
)

$ErrorActionPreference = "Stop"

function Get-AttributeValue {
    param(
        [System.Xml.XmlElement]$Element,
        [string]$Name
    )

    if ($null -eq $Element -or -not $Element.HasAttribute($Name)) {
        return $null
    }
    return $Element.GetAttribute($Name)
}

try {
    $application = $null
    $connection = "active_object"
    try {
        $application = [Runtime.InteropServices.Marshal]::GetActiveObject("OneNote.Application")
    }
    catch {
        $connection = "new_com_object"
        $application = New-Object -ComObject OneNote.Application
    }

    $windows = $application.Windows
    $current = $windows.CurrentWindow
    $currentPageId = [string]$current.CurrentPageId
    if ([string]::IsNullOrWhiteSpace($currentPageId)) {
        throw "OneNote COM returned no CurrentPageId (Windows.Count=$([int]$windows.Count))."
    }

    $pageMetadata = $null
    try {
        $pageXml = ""
        $application.GetPageContent($currentPageId, [ref]$pageXml, 0, 2)
        [xml]$pageDocument = $pageXml
        $page = $pageDocument.DocumentElement
        $pageMetadata = [ordered]@{
            readable = $true
            id = Get-AttributeValue $page "ID"
            name = Get-AttributeValue $page "name"
            page_level = Get-AttributeValue $page "pageLevel"
            last_modified_time = Get-AttributeValue $page "lastModifiedTime"
            is_in_recycle_bin = Get-AttributeValue $page "isInRecycleBin"
            xml_characters = $pageXml.Length
        }
    }
    catch {
        $pageMetadata = [ordered]@{
            readable = $false
            hresult = $_.Exception.HResult
            error = $_.Exception.Message
        }
    }

    [ordered]@{
        ok = $true
        connection = $connection
        windows_count = [int]$windows.Count
        expected_page_id = $ExpectedPageId
        current_page_id = $currentPageId
        matches_expected_page_id = $currentPageId -eq $ExpectedPageId
        current_section_id = [string]$current.CurrentSectionId
        current_section_group_id = [string]$current.CurrentSectionGroupId
        current_notebook_id = [string]$current.CurrentNotebookId
        page_metadata = $pageMetadata
    } | ConvertTo-Json -Depth 8
}
catch {
    [ordered]@{
        ok = $false
        expected_page_id = $ExpectedPageId
        hresult = $_.Exception.HResult
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 8
    exit 2
}
