function updateAverageRating(msg) {
    if (msg.ratingFrequency == 0)
        $("#average-rating-section").css("display", "none");
        
    else {
        $("#average-rating-section").css("display", "inline");
        $("#rating-frequency").text(msg.ratingFrequency);
        $("#average-rating").text((msg.ratingSum / msg.ratingFrequency).toFixed(1));
        $("#user-demonym").text(msg.ratingFrequency == 1 ? "user" : "users");
    }
}

function unrateRelease(clicked_element, release_id) {
    $.ajax({
        method: "POST",
        url: "/unrate/" + release_id
        
    }).done(function (msg) {
        if (msg.error)
            return;
            
        clicked_element.classList.remove("selected");
        updateAverageRating(msg);
    })
}

function rateRelease(clicked_element, release_id, rating) {
    /*User clicked the already selected rating
       => unrate*/
    if (clicked_element.classList.contains("selected")) {
        unrateRelease(clicked_element, release_id);
        return;
    }
    
    $.ajax({
        method: "POST",
        url: "/rate/" + release_id + "/" + rating
        
    }).done(function (msg) {
        if (msg.error)
            return;
            
        /*Success, update rating widget*/
        
        var siblings = clicked_element.parentNode.children;
        
        for (var i = 0; i < siblings.length; i++)
            siblings[i].classList.remove("selected");
            
        clicked_element.classList.add("selected");
        
        updateAverageRating(msg);
    });
}