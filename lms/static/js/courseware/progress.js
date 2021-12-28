$(document).ready(function() {
    let user_progress = $("#circle").data('user-progress')
    if(user_progress == '100'){
        $(".progress-circle .circle").addClass('larger')
    }
    $("#collapse1").addClass("show")
    $("#heading1 .collapsed").removeClass("collapsed")
    $(".progress-tab").click(function(pEvent){
        let link_child = $(this).children(":first");
        let id_for_container_to_show = link_child.attr("href")
        pEvent.preventDefault();
        $(".progress-tab").removeClass('active')
        $(this).addClass('active')
        // hide all containers except the one for current active tab
        $(".progress-navigation-bars").addClass('hidden')
        $(id_for_container_to_show).removeClass('hidden')
    });
});
