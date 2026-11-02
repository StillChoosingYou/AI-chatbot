/* Any button marked data-print opens the browser's print dialog (Print / Save as PDF).
   A file instead of onclick="window.print()", so the Content-Security-Policy can refuse inline handlers. */
document.addEventListener('click', function (event) {
  if (event.target.closest && event.target.closest('[data-print]')) window.print();
});
