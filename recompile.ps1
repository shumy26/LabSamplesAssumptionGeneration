python compile_to_spectra.py LabSamples.gm LabSamples.spectra

Write-Host "Checking realizability with Spectra..."

Set-Location spectra
$CHECK_REALIZABILITY = (java -jar spectra-cli.jar -i ../LabSamples.spectra --counter-strategy 2>&1 | Out-String)
Set-Location ..

if ($CHECK_REALIZABILITY -like "*Specification is realizable*") {
    Write-Host "Result: REALIZABLE!"
    $CHECK_REALIZABILITY | Out-File -Encoding utf8 counter_strategy.txt
    python llm_checker.py --model "gemma4:26b"
} elseif ($CHECK_REALIZABILITY -like "*Specification is unrealizable*") {
    Write-Host "Result: UNREALIZABLE! Extracting counter-strategy..."
    $CHECK_REALIZABILITY | Out-File -Encoding utf8 counter_strategy.txt
    Write-Host "Failed: Environment winning strategy saved to counter_strategy.txt"
    python llm_checker.py --model "gemma4:26b"
} else {
    Write-Host "An unexpected error occurred during the Spectra check:"
    Write-Host $CHECK_REALIZABILITY
}
