/* global frappe */

frappe.pages["unity-helpdesk"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "Unity Helpdesk",
    single_column: true,
  });

  wrapper.unity_helpdesk_page = page;
  page.body.addClass("unity-helpdesk-desk-page");
  page.body.css({
    padding: "0",
    overflow: "hidden",
    background: "#f3f6fb",
  });
  const iframe = document.createElement("iframe");
  iframe.src = "/unity-helpdesk";
  iframe.title = "Unity Helpdesk";
  iframe.style.cssText =
    "width:100%;height:calc(100vh - 112px);border:0;display:block;background:#f3f6fb";
  page.body[0].appendChild(iframe);
};
