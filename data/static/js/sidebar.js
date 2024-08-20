/* Set the width of the sidebar to 250px and the left margin of the page content to 250px */
function openNav() {
  let width = window.innerWidth;
  document.getElementById("mySidebar").style.width = "270px";
  document.getElementById("main").style.marginLeft = "0%";
}

/* Set the width of the sidebar to 0 and the left margin of the page content to 0 */
function closeNav() {
  document.getElementById("mySidebar").style.width = "0%";
  document.getElementById("main").style.marginLeft = "0%";
}

document.onclick = function(e) {
  let sidebar = document.querySelector(".sidebar");
  let openbtn = document.querySelector(".openbtn");

  console.log(e.target);
  console.log(window.innerWidth);
  if (!sidebar.contains(e.target) && !openbtn.contains(e.target)) {
    document.getElementById("mySidebar").style.width = "0";
    document.getElementById("main").style.marginLeft = "0";
  }
}
