# Run locally in PowerShell. Input is hidden and is never written to project files.
$secureKey = Read-Host 'Paste your Gemini API key (hidden input)' -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $keyText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    if ([string]::IsNullOrWhiteSpace($keyText)) { throw 'No key entered.' }
    [Environment]::SetEnvironmentVariable('GEMINI_API_KEY', $keyText, 'User')
    [Environment]::SetEnvironmentVariable('GEMINI_API_KEY', $keyText, 'Process')
    Write-Host 'GEMINI_API_KEY saved in your Windows user environment. Open a new terminal for future runs.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    Remove-Variable keyText -ErrorAction SilentlyContinue
    $secureKey.Dispose()
}
